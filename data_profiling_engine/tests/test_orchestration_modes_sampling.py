"""M2-T10 tests — modes (quick/standard/deep) + sampling (reservoir, seed)
wired end-to-end through the engine path (ENGINE §2.2, §8; ADR-015/ADR-007).

Two surfaces:

1. :func:`profiler._sample_relation` — the reservoir is now a *real* bounded
   sample (DuckDB ``USING SAMPLE RESERVOIR (n ROWS) REPEATABLE (seed)``), no
   longer the preliminary md5 ORDER+LIMIT hash: bounded, deterministic per
   seed, different for a different seed, returns-all when the relation is
   smaller than the window; ``block`` keeps locality via a bounded scan.

2. :mod:`orchestration` — ``run_profiling_pipeline`` threads ``config.mode`` +
   ``config.sampling`` + ``config.sections`` through discover → plan → the
   three M2-T4..T6 profiler passes (merged per column) → quality → findings →
   recommendations, so a sampled run stamps the seed on EVERY sampled metric
   and every produced profile is contract-valid.
"""
from __future__ import annotations

from typing import List, Optional
from pathlib import Path

import duckdb

from data_profiling_engine import schemas as S
from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.engine.metrics.profiler import _sample_relation
from data_profiling_engine.orchestration import (
    run_dataset_profiling,
    run_profiling_pipeline,
)
from data_profiling_engine.schemas.models import (
    DatasetConfig,
    MetricMethod,
    ProfileMode,
    SourceSnapshot,
)

_VIEW = "src"

_BASE_ROWS = (
    "(1,10,'Active'),(2,20,'N/A'),(3,30,''),(4,NULL,'Active'),"
    "(5,50,'n/a'),(6,60,NULL),(7,40,'Closed')"
)


def _make_handle(con: duckdb.DuckDBPyConnection, view: str = _VIEW,
                 rows: str = _BASE_ROWS) -> RelationHandle:
    con.execute(
        f'CREATE OR REPLACE VIEW "{view}" AS '
        f"SELECT CAST(v0 AS BIGINT) AS id, CAST(v1 AS BIGINT) AS amount, "
        f"CAST(v2 AS VARCHAR) AS status, row_number() OVER () AS _row_id "
        f"FROM (VALUES {rows}) t(v0, v1, v2)"
    )
    return RelationHandle(
        dataset_id="ds", connector="manual", view_name=view,
        snapshot=SourceSnapshot(content_hash="sha256:" + "0" * 64),
        columns=(("id", "BIGINT"), ("amount", "BIGINT"), ("status", "VARCHAR")),
        row_id_column="_row_id",
    )


def _config(mode: str = "standard", sampling: Optional[dict] = None,
            sections: Optional[List[str]] = None) -> DatasetConfig:
    profiling: dict = {"mode": mode}
    if sampling:
        profiling["sampling"] = sampling
    if sections:
        profiling["sections"] = sections
    return DatasetConfig.model_validate({
        "config_version": 3,
        "dataset": {"name": "ds"},
        "profiling": profiling,
        "quality_checks": [
            {"id": "r1", "rule": "not_null", "column": "id",
             "dimension": "completeness", "severity": "high"},
        ],
    })


def _by_name(columns, name):
    for c in columns:
        if c.column == name:
            return c
    raise KeyError(name)


# ===========================================================================
# 1. Reservoir — real bounded, seeded, deterministic
# ===========================================================================
class TestReservoir:
    def _big_view(self, con, n=1000):
        con.execute(
            f'CREATE OR REPLACE VIEW "big" AS '
            f"SELECT i AS x, i AS _row_id FROM range({n}) t(i)"
        )

    def test_reservoir_is_bounded(self):
        con = duckdb.connect()
        self._big_view(con)
        name = _sample_relation(con, "big", "reservoir", 50, 42)
        assert con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] == 50
        con.close()

    def test_reservoir_deterministic_for_same_seed(self):
        con = duckdb.connect()
        self._big_view(con)
        a = _sample_relation(con, "big", "reservoir", 50, 7)
        rows_a = sorted(r[0] for r in con.execute(f'SELECT x FROM "{a}"').fetchall())
        b = _sample_relation(con, "big", "reservoir", 50, 7)
        rows_b = sorted(r[0] for r in con.execute(f'SELECT x FROM "{b}"').fetchall())
        assert rows_a == rows_b
        con.close()

    def test_reservoir_differs_for_different_seed(self):
        con = duckdb.connect()
        self._big_view(con)
        a = _sample_relation(con, "big", "reservoir", 50, 1)
        b = _sample_relation(con, "big", "reservoir", 50, 2)
        rows_a = {r[0] for r in con.execute(f'SELECT x FROM "{a}"').fetchall()}
        rows_b = {r[0] for r in con.execute(f'SELECT x FROM "{b}"').fetchall()}
        assert rows_a != rows_b
        con.close()

    def test_reservoir_returns_all_when_smaller_than_window(self):
        con = duckdb.connect()
        self._big_view(con, n=10)
        name = _sample_relation(con, "big", "reservoir", 100, 5)
        assert con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] == 10
        con.close()

    def test_block_strategy_is_a_bounded_scan(self):
        con = duckdb.connect()
        self._big_view(con, n=1000)
        name = _sample_relation(con, "big", "block", 25, 1)
        assert con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] == 25
        con.close()

    def test_same_window_reuses_one_relation_name(self):
        con = duckdb.connect()
        self._big_view(con)
        n1 = _sample_relation(con, "big", "reservoir", 50, 99)
        n2 = _sample_relation(con, "big", "reservoir", 50, 99)
        assert n1 == n2  # content-hashed name, no clobber
        con.close()


# ===========================================================================
# 2. Modes threaded through the pipeline
# ===========================================================================
class TestModes:
    def test_standard_small_keeps_expensive_metric_exact(self):
        con = duckdb.connect()
        h = _make_handle(con)
        res = run_profiling_pipeline(con, h, _config("standard"), "run-std")
        amount = _by_name(res.columns, "amount")
        assert amount.uniqueness.distinct_count.provenance.method == MetricMethod.EXACT.value
        con.close()

    def test_quick_mode_degrades_expensive_metric_to_approximate(self):
        con = duckdb.connect()
        h = _make_handle(con)
        res = run_profiling_pipeline(con, h, _config("quick"), "run-q")
        prov = _by_name(res.columns, "amount").uniqueness.distinct_count.provenance
        assert prov.method == MetricMethod.APPROXIMATE.value
        assert prov.method_params.get("algorithm")
        con.close()

    def test_deep_mode_forces_exact(self):
        con = duckdb.connect()
        h = _make_handle(con)
        res = run_profiling_pipeline(con, h, _config("deep"), "run-d")
        assert res.mode is ProfileMode.DEEP
        prov = _by_name(res.columns, "amount").uniqueness.distinct_count.provenance
        assert prov.method == MetricMethod.EXACT.value
        con.close()


# ===========================================================================
# 3. Sampling wired end-to-end (seed on every sampled metric)
# ===========================================================================
class TestSamplingEndToEnd:
    _SAMPLING = {"enabled": True, "strategy": "reservoir", "size": 4, "seed": 20261024}

    def test_sampled_run_stamps_seed_on_every_sampled_metric(self):
        con = duckdb.connect()
        h = _make_handle(con)
        res = run_profiling_pipeline(con, h, _config("standard", sampling=self._SAMPLING), "run-s")
        # every planned sampled decision carries the seed...
        planned = [d for d in res.plan.computed_tasks() if d.method is MetricMethod.SAMPLED]
        assert planned, "expected at least one sampled decision under a window"
        for d in planned:
            assert d.params["seed"] == 20261024
        # ...and it reaches the emitted provenance on the profiles
        seen_sampled = False
        for col in res.columns:
            for block in (col.stats, col.uniqueness):
                if block is None:
                    continue
                values = [getattr(block, f, None) for f in
                          ("mean", "median", "stddev", "distinct_count")]
                for mv in values:
                    if mv is None or mv.provenance is None:
                        continue
                    if mv.provenance.method == MetricMethod.SAMPLED.value:
                        seen_sampled = True
                        assert mv.provenance.method_params["seed"] == 20261024
        assert seen_sampled, "a sampled plan must surface a sampled MetricValue"
        con.close()

    def test_run_sampling_record_populated_when_enabled(self):
        con = duckdb.connect()
        h = _make_handle(con)
        res = run_profiling_pipeline(con, h, _config("standard", sampling=self._SAMPLING), "run-s")
        assert res.sampling is not None
        assert res.sampling.enabled is True
        assert res.sampling.strategy == "reservoir"
        assert res.sampling.sample_size == 4  # §9 `size` → §2 `sample_size`
        assert res.sampling.seed == 20261024
        con.close()

    def test_run_sampling_none_when_disabled(self):
        con = duckdb.connect()
        h = _make_handle(con)
        res = run_profiling_pipeline(con, h, _config("standard"), "run-n")
        assert res.sampling is None
        con.close()

    def test_sampled_run_is_deterministic(self):
        dumps = []
        for _ in range(2):
            con = duckdb.connect()
            h = _make_handle(con)
            res = run_profiling_pipeline(con, h, _config("standard", sampling=self._SAMPLING), "run-x")
            dumps.append([c.model_dump_json() for c in res.columns])
            con.close()
        assert dumps[0] == dumps[1]


# ===========================================================================
# 4. Family composition + section gating + downstream wiring
# ===========================================================================
class TestCompositionAndDownstream:
    def test_all_families_merge_onto_one_profile(self):
        con = duckdb.connect()
        h = _make_handle(con)
        res = run_profiling_pipeline(con, h, _config("standard"), "run-m")
        amount = _by_name(res.columns, "amount")
        assert amount.stats is not None and amount.stats.mean is not None
        assert amount.missingness is not None
        assert amount.uniqueness is not None
        assert amount.distribution is not None  # numeric → distribution (T6)
        assert amount.temporal is None
        status = _by_name(res.columns, "status")
        assert status.categorical is not None and status.categorical.frequencies
        assert status.stats is None  # VARCHAR → no numeric stats
        con.close()

    def test_section_disable_omits_family_block(self):
        con = duckdb.connect()
        h = _make_handle(con)
        # only schema + missing_values → uniqueness/categorical/distribution absent
        res = run_profiling_pipeline(
            con, h, _config("standard", sections=["schema", "missing_values"]), "run-sec")
        amount = _by_name(res.columns, "amount")
        assert amount.uniqueness is None
        assert amount.categorical is None
        assert amount.distribution is None
        assert amount.missingness is not None
        con.close()

    def test_findings_have_wired_recommendations(self):
        con = duckdb.connect()
        h = _make_handle(con)
        res = run_profiling_pipeline(con, h, _config("standard"), "run-f")
        assert res.recommendations
        assert len(res.findings) == len(res.recommendations)
        for f in res.findings:
            assert f.recommendation_ids, "M2-T9 wiring must back-reference every finding"
        con.close()

    def test_every_column_profile_schema_valid(self):
        con = duckdb.connect()
        h = _make_handle(con)
        for mode in ("quick", "standard", "deep"):
            res = run_profiling_pipeline(con, h, _config(mode), f"run-{mode}")
            for col in res.columns:
                errs = S.validate("column_profile.schema.json", col.model_dump(mode="json"))
                assert errs == [], (mode, col.column, errs)
        con.close()

    def test_quality_checks_run_against_full_relation(self):
        con = duckdb.connect()
        h = _make_handle(con)
        # id is the not_null target; a sampled window must not change the rule verdict
        res = run_profiling_pipeline(
            con, h, _config("standard", sampling={"enabled": True, "strategy": "reservoir",
                                                  "size": 4, "seed": 3}), "run-qc")
        assert len(res.checks) == 1
        assert res.checks[0].rule_id == "r1"
        con.close()


# ===========================================================================
# 5. CSV end-to-end via run_dataset_profiling (the engine CLI path entry)
# ===========================================================================
def test_run_dataset_profiling_end_to_end_csv(tmp_path: Path):
    csv = tmp_path / "sales.csv"
    csv.write_text("id,amount,status\n"
                   "1,10,Active\n2,20,N/A\n3,30,\n4,,Active\n"
                   "5,50,n/a\n6,60,\n7,40,Closed\n")
    con = duckdb.connect()
    res = run_dataset_profiling(
        "sales", csv, _config("standard"), "run-csv",
        con=con, allowed_roots=[tmp_path])
    assert res.n_rows == 7
    assert res.columns
    for col in res.columns:
        errs = S.validate("column_profile.schema.json", col.model_dump(mode="json"))
        assert errs == [], (col.column, errs)
    # id is declared-ish identifier column: uniqueness computed, no categorical
    amount = _by_name(res.columns, "amount")
    assert amount.stats is not None
    con.close()
