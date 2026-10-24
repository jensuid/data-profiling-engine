"""M2-T4 reference tests — statistical + missingness profilers.

TESTING §3.1: every executed metric is asserted against an **independent
reference** (hand-computed literals + Polars), never against the engine's own
SQL. The fixture is 7 hand-checkable rows so each number can be verified by
hand (see HAND bookkeeping below). Coverage:

- Statistical scalars on a numeric column (count/min/max/mean/median/quantiles/
  stddev) vs Polars `std(ddof=1)` / `quantile(linear)` + literals.
- Missingness null/blank/sentinel counts + missing_rate + denominator.
- ADR-017 sentinel semantics: absent set ⇒ 0; case-sensitive exact match only.
- Provenance honesty: exact / approximate (t-digest, quick mode) / sampled
  (seed recorded, bounded relation, deterministic) / not_computed (reason, no
  value) — ADR-015.
- §6.4 partial failure: one un-executable metric does not abort the others.
- Every emitted ColumnProfile validates against the frozen §3.2 schema.

HAND fixture (id, amount, status) — 7 rows:
  1  10  'Active'
  2  20  'N/A'      sentinel
  3  30  ''         blank
  4  NULL 'Active'  amount null
  5  50  'n/a'      sentinel (different case)
  6  60  NULL       status null
  7  40  'Closed'
amount non-null {10,20,30,50,60,40}: count 6, min 10, max 60, mean 35.0,
  sorted {10,20,30,40,50,60} median 35.0, p25 22.5, p75 47.5, stddev sqrt(350).
status: null_count 1, blank_count 1, sentinel_count 2 ({N/A, n/a}),
  missing union = null(1)+blank(1)+sentinel(2) = 4 → rate 4/7.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import List, Optional

import duckdb
import polars as pl
import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery import discover
from data_profiling_engine.engine.metrics import MetricRegistry, default_registry
from data_profiling_engine.engine.metrics.profiler import execute_stat_and_missingness
from data_profiling_engine.engine.planner import plan_execution
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.schemas.models import (
    ColumnProfile,
    DatasetConfig,
    MetricMethod,
    ProfileMode,
    SourceSnapshot,
)

AMOUNT_NONNULL = [10, 20, 30, 50, 60, 40]
STDDEV_SAMP = math.sqrt(350)  # hand: deviations {25,15,5,5,15,25}, Σsq=1750, /5

_BASE_ROWS = (
    "(1,10,'Active'),(2,20,'N/A'),(3,30,''),(4,NULL,'Active'),"
    "(5,50,'n/a'),(6,60,NULL),(7,40,'Closed')"
)


def _snapshot() -> SourceSnapshot:
    return SourceSnapshot(content_hash="sha256:" + "0" * 64)


def _handle(con: duckdb.DuckDBPyConnection, view: str,
            rows: str = _BASE_ROWS,
            columns: tuple = (("id", "BIGINT"), ("amount", "BIGINT"),
                              ("status", "VARCHAR"))) -> RelationHandle:
    con.execute(
        f'CREATE OR REPLACE VIEW "{view}" AS '
        f"SELECT CAST(v0 AS BIGINT) AS id, CAST(v1 AS {columns[1][1]}) AS amount, "
        f"CAST(v2 AS VARCHAR) AS status, row_number() OVER () AS _row_id "
        f"FROM (VALUES {rows}) t(v0, v1, v2)"
    )
    return RelationHandle(
        dataset_id="ds", connector="manual", view_name=view, snapshot=_snapshot(),
        columns=columns, row_id_column="_row_id",
    )


def _config(sentinel: Optional[List[str]] = None, mode: str = "standard",
            sampling: Optional[dict] = None, sections: Optional[List[str]] = None
            ) -> DatasetConfig:
    missing = {"sentinel_values": sentinel} if sentinel is not None else None
    profiling = {"mode": mode}
    if missing:
        profiling["missing"] = missing
    if sampling:
        profiling["sampling"] = sampling
    if sections:
        profiling["sections"] = sections
    return DatasetConfig.model_validate({
        "config_version": 3,
        "dataset": {"name": "ds"},
        "profiling": profiling,
        "quality_checks": [{"id": "r", "rule": "not_null", "column": "id",
                            "dimension": "completeness", "severity": "high"}],
    })


def _run(con, h, cfg, mode=ProfileMode.STANDARD, registry=None, n_rows=7,
         sampling=None, sections=None):
    report = discover(con, h, cfg)
    plan = plan_execution(
        report, mode, n_rows,
        sampling if sampling is not None else (cfg.profiling.sampling if cfg else None),
        sections if sections is not None else (cfg.profiling.sections if cfg else None),
        registry or default_registry(),
    )
    return execute_stat_and_missingness(con, h, report, plan, cfg, registry)


def _by_name(profiles, name):
    for p in profiles:
        if p.column == name:
            return p
    raise AssertionError(f"column {name!r} missing")


# ---------------------------------------------------------------------------
# Statistical profiler vs independent references
# ---------------------------------------------------------------------------


class TestStatistical:
    def setup_method(self):
        self.con = duckdb.connect()
        self.h = _handle(self.con, "src")
        self.cfg = _config()
        res = _run(self.con, self.h, self.cfg)
        self.amount = _by_name(res.profiles, "amount")
        self.errors = res.errors
        self.con.close()

    def test_no_execution_errors(self):
        assert self.errors == ()

    def test_stats_match_polars_independent(self):
        s = pl.Series("amount", [10, 20, 30, None, 50, 60, 40])
        st = self.amount.stats
        assert st.valid_count == s.drop_nulls().len() == 6
        assert st.min.value == s.min() == 10
        assert st.max.value == s.max() == 60
        assert st.mean.value == pytest.approx(s.mean())
        assert st.median.value == pytest.approx(s.median())
        assert st.p25.value == pytest.approx(s.quantile(0.25, interpolation="linear"))
        assert st.p75.value == pytest.approx(s.quantile(0.75, interpolation="linear"))
        assert st.stddev.value == pytest.approx(s.std(ddof=1))

    def test_stats_match_hand_computed(self):
        st = self.amount.stats
        assert st.mean.value == 35.0
        assert st.median.value == 35.0
        assert st.p25.value == 22.5
        assert st.p75.value == 47.5
        assert st.stddev.value == pytest.approx(STDDEV_SAMP)

    def test_all_stats_exact_provenance(self):
        st = self.amount.stats
        for mv in (st.min, st.max, st.mean, st.median, st.p25,
                   st.p75, st.p95, st.p99, st.stddev):
            assert mv.provenance.method == MetricMethod.EXACT.value
            assert not mv.provenance.method_params

    def test_categorical_column_has_no_stats_block(self):
        # status is categorical_nominal → statistical metrics gated out (§4 gate 1)
        assert self.amount.stats is not None
        con = duckdb.connect()
        h = _handle(con, "src")
        profs = _run(con, h, _config()).profiles
        assert _by_name(profs, "status").stats is None
        con.close()


# ---------------------------------------------------------------------------
# Missingness profiler
# ---------------------------------------------------------------------------


class TestMissingness:
    def setup_method(self):
        self.con = duckdb.connect()
        self.h = _handle(self.con, "src")
        self.res = _run(self.con, self.h, _config(sentinel=["N/A", "n/a"]))
        self.status = _by_name(self.res.profiles, "status")
        self.amount = _by_name(self.res.profiles, "amount")
        self.con.close()

    def test_varchar_counts_hand_computed(self):
        m = self.status.missingness
        assert m.null_count == 1        # row 6 explicit NULL
        assert m.blank_count == 1       # row 3 ''
        assert m.sentinel_count == 2    # N/A + n/a

    def test_missing_rate_is_union_with_denominator(self):
        m = self.status.missingness
        assert m.missing_rate.denominator == 7
        # null(1)+blank(1)+sentinel(2) = 4 distinct rows, counted once each
        assert m.missing_rate.value == pytest.approx(4 / 7)

    def test_numeric_column_blank_gated_to_zero(self):
        m = self.amount.missingness
        assert m.null_count == 1
        assert m.blank_count == 0       # blank_count spec is varchar-only
        assert m.sentinel_count == 0    # numeric values never match sentinels
        assert m.missing_rate.value == pytest.approx(1 / 7)


class TestSentinelADR017:
    def test_absent_sentinel_set_yields_zero(self):
        con = duckdb.connect()
        h = _handle(con, "src")
        profs = _run(con, h, _config(sentinel=None)).profiles
        assert _by_name(profs, "status").missingness.sentinel_count == 0
        con.close()

    def test_case_sensitive_exact_only(self):
        con = duckdb.connect()
        h = _handle(con, "src")
        # only 'N/A' declared → the 'n/a' row must NOT match (exact, case-sensitive)
        profs = _run(con, h, _config(sentinel=["N/A"])).profiles
        assert _by_name(profs, "status").missingness.sentinel_count == 1
        con.close()

    def test_sentinel_values_are_bound_not_interpolated(self):
        """A value containing an SQL quote still matches literally (executemany
        $1 binding, ADR-017 — user data never breaks the statement)."""
        con = duckdb.connect()
        con.execute(
            'CREATE VIEW "srcq" AS SELECT CAST(v0 AS BIGINT) AS id, '
            "CAST(v1 AS BIGINT) AS amount, CAST(v2 AS VARCHAR) AS status, "
            "row_number() OVER () AS _row_id FROM (VALUES "
            "(1,10,'it''s'),(2,20,'nope'),(3,30,'it''s')) t(v0,v1,v2)"
        )
        h = RelationHandle(
            dataset_id="ds", connector="manual", view_name="srcq",
            snapshot=_snapshot(),
            columns=(("id", "BIGINT"), ("amount", "BIGINT"), ("status", "VARCHAR")),
            row_id_column="_row_id",
        )
        profs = _run(con, h, _config(sentinel=["it's"]), n_rows=3).profiles
        st = _by_name(profs, "status")
        assert st.missingness.sentinel_count == 2
        con.close()


# ---------------------------------------------------------------------------
# Provenance honesty across methods (ADR-015)
# ---------------------------------------------------------------------------


class TestMethodHonesty:
    def test_quick_mode_quantiles_are_approximate_with_algorithm(self):
        con = duckdb.connect()
        h = _handle(con, "src")
        profs = _run(con, h, _config(mode="quick"), mode=ProfileMode.QUICK).profiles
        med = _by_name(profs, "amount").stats.median
        assert med.provenance.method == MetricMethod.APPROXIMATE.value
        assert med.provenance.method_params["algorithm"]
        # approximate t-digest on 6 values: bounded sanity, not exactness
        assert med.value == pytest.approx(35.0, abs=12)
        con.close()

    def test_sampled_decision_runs_on_bounded_relation_with_seed(self):
        con = duckdb.connect()
        h = _handle(con, "src")
        # force stats.mean to be sampled-eligible (default is exact_under_sampling)
        reg = MetricRegistry([
            replace(s, exact_under_sampling=False) if s.metric_id == "stats.mean"
            else s for s in default_registry().specs
        ])
        sampling = {"enabled": True, "strategy": "reservoir", "size": 4, "seed": 20261024}
        cfg = _config(sampling=sampling)
        res = _run(con, h, cfg, registry=reg)
        mean = _by_name(res.profiles, "amount").stats.mean
        assert mean.provenance.method == MetricMethod.SAMPLED.value
        assert mean.provenance.method_params["seed"] == 20261024
        assert res.sample_view is not None
        n = con.execute(f'SELECT count(*) FROM "{res.sample_view}"').fetchone()[0]
        assert n <= 4
        # sample mean is a subset of the true values → stays within the range
        assert 10 <= mean.value <= 60
        con.close()

    def test_sampled_is_deterministic_for_same_seed(self):
        reg = MetricRegistry([
            replace(s, exact_under_sampling=False) if s.metric_id == "stats.mean"
            else s for s in default_registry().specs
        ])
        values = []
        for _ in range(2):
            con = duckdb.connect()
            h = _handle(con, "src")
            cfg = _config(sampling={"enabled": True, "strategy": "reservoir",
                                    "size": 4, "seed": 7})
            res = _run(con, h, cfg, registry=reg)
            values.append(_by_name(res.profiles, "amount").stats.mean.value)
            con.close()
        assert values[0] == pytest.approx(values[1])

    def test_not_computed_carries_reason_and_no_value(self):
        con = duckdb.connect()
        h = _handle(con, "src")
        # disable descriptive_statistics section → stats decisions not_computed
        res = _run(con, h, _config(), sections=["schema", "missing_values"])
        amount = _by_name(res.profiles, "amount")
        # stats block metrics are structurally skipped → not_computed, value absent
        if amount.stats is not None and amount.stats.mean is not None:
            assert amount.stats.mean.provenance.method == MetricMethod.NOT_COMPUTED.value
            assert amount.stats.mean.provenance.reason
            assert "value" not in amount.stats.mean.model_dump()
        else:
            assert amount.stats is None or amount.stats.mean is None
        # missingness still computed (section active)
        assert amount.missingness is not None
        con.close()


# ---------------------------------------------------------------------------
# Partial-failure policy (§6.4)
# ---------------------------------------------------------------------------


class TestPartialFailure:
    def test_one_bad_metric_does_not_abort_the_run(self):
        con = duckdb.connect()
        h = _handle(con, "src")
        reg = MetricRegistry([
            replace(s, sql_template="SELECT no_such_fn({col}) FROM {table}")
            if s.metric_id == "stats.max" else s for s in default_registry().specs
        ])
        res = _run(con, h, _config(), registry=reg)
        amount = _by_name(res.profiles, "amount")
        # stats.max failed → recorded as an error and marked not_computed
        assert any(e.task and e.task.startswith("stats.max") for e in res.errors)
        err = next(e for e in res.errors if e.task.startswith("stats.max"))
        assert err.code == ErrorCode.METRIC_NOT_COMPUTED.value
        assert amount.stats.max.provenance.method == MetricMethod.NOT_COMPUTED.value
        # sibling metric still computed (run not aborted)
        assert amount.stats.min.value == 10
        con.close()


# ---------------------------------------------------------------------------
# Edge cases + DECIMAL transport (TESTING §3.1 / §3.3)
# ---------------------------------------------------------------------------


class TestEdges:
    def test_empty_relation_metrics(self):
        con = duckdb.connect()
        con.execute(
            'CREATE VIEW "srce" AS SELECT CAST(v0 AS BIGINT) AS id, '
            "CAST(v1 AS BIGINT) AS amount, CAST(v2 AS VARCHAR) AS status, "
            "row_number() OVER () AS _row_id FROM (VALUES (1,1,'x')) t(v0,v1,v2) "
            "WHERE false"
        )
        h = RelationHandle(
            dataset_id="ds", connector="manual", view_name="srce",
            snapshot=_snapshot(),
            columns=(("id", "BIGINT"), ("amount", "BIGINT"), ("status", "VARCHAR")),
            row_id_column="_row_id",
        )
        profs = _run(con, h, _config(), n_rows=0).profiles
        amount = _by_name(profs, "amount")
        assert amount.stats.valid_count == 0
        assert amount.stats.min.value is None
        assert amount.missingness.missing_rate.value == 0.0
        assert amount.missingness.missing_rate.denominator == 0
        con.close()

    def test_decimal_column_normalized_to_float_and_serialisable(self):
        con = duckdb.connect()
        con.execute(
            'CREATE VIEW "srcd" AS SELECT CAST(v0 AS BIGINT) AS id, '
            "CAST(v1 AS DECIMAL(10,2)) AS amount, CAST(v2 AS VARCHAR) AS status, "
            "row_number() OVER () AS _row_id FROM (VALUES "
            "(1,'10.50','a'),(2,'20.25','b'),(3,'30.00','c')) t(v0,v1,v2)"
        )
        h = RelationHandle(
            dataset_id="ds", connector="manual", view_name="srcd",
            snapshot=_snapshot(),
            columns=(("id", "BIGINT"), ("amount", "DECIMAL"), ("status", "VARCHAR")),
            row_id_column="_row_id",
        )
        profs = _run(con, h, _config(), n_rows=3).profiles
        amount = _by_name(profs, "amount")
        assert isinstance(amount.stats.mean.value, float)
        # JSON-serialisable (no Decimal leak)
        import json
        json.dumps(amount.model_dump(mode="json"))
        con.close()


# ---------------------------------------------------------------------------
# Contract conformance + determinism (TESTING §3.3)
# ---------------------------------------------------------------------------


class TestContractConformance:
    def test_every_profile_schema_valid(self):
        con = duckdb.connect()
        h = _handle(con, "src")
        for sentinel in (None, ["N/A", "n/a"]):
            for mode in (ProfileMode.STANDARD, ProfileMode.QUICK):
                res = _run(con, h, _config(sentinel=sentinel, mode=mode.value), mode=mode)
                for p in res.profiles:
                    errs = S.validate("column_profile.schema.json",
                                      p.model_dump(mode="json"))
                    assert errs == [], (sentinel, mode, p.column, errs)
        con.close()

    def test_identical_rerun_is_byte_stable(self):
        con = duckdb.connect()
        h = _handle(con, "src")
        a = _run(con, h, _config(sentinel=["N/A", "n/a"])).profiles
        b = _run(con, h, _config(sentinel=["N/A", "n/a"])).profiles
        assert [p.model_dump_json() for p in a] == [p.model_dump_json() for p in b]
        con.close()


# ---------------------------------------------------------------------------
# ADR-017 config surface (dataset_config schema + pydantic)
# ---------------------------------------------------------------------------


class TestSentinelConfigSurface:
    def _base(self, missing):
        return {
            "config_version": 1, "dataset": {"name": "d"},
            "profiling": {"mode": "standard", "missing": missing},
            "quality_checks": [{"id": "r", "rule": "not_null", "column": "c",
                                "dimension": "completeness", "severity": "low"}],
        }

    def test_valid_sentinel_values(self):
        assert S.validate("dataset_config.schema.json",
                          self._base({"sentinel_values": ["N/A", "-"]})) == []

    def test_rejects_duplicate_sentinels(self):
        errs = S.validate("dataset_config.schema.json",
                          self._base({"sentinel_values": ["x", "x"]}))
        assert errs

    def test_rejects_empty_sentinel_string(self):
        errs = S.validate("dataset_config.schema.json",
                          self._base({"sentinel_values": ["ok", ""]}))
        assert errs

    def test_rejects_unknown_missing_key(self):
        errs = S.validate("dataset_config.schema.json",
                          self._base({"sentinel_values": ["x"], "bogus": 1}))
        assert errs
