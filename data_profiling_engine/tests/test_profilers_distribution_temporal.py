"""M2-T6 reference tests — distribution + single-column temporal profilers.

TESTING §3.1: every executed metric is asserted against an **independent
reference** (hand-computed literals + plain-Python statistics + Polars), never
against the engine's own SQL. Coverage:

- Distribution.histogram — fixed-bin ``width_bucket`` emulation (DuckDB has no
  width_bucket scalar, STATE gotcha): bin count = HISTOGRAM_BIN_COUNT, the bin
  **sum invariant** equals the non-null count, edges span [min, max], the bin
  strategy is recorded in ``method_params`` (§2.2), a constant column collapses
  to a single bin, an empty column is an honest ``not_computed`` (bins/counts
  present but empty — never a fabricated distribution).
- Distribution.skewness — equals the **adjusted Fisher–Pearson G1** DuckDB
  computes (distinct from Polars' biased ``g1``), within tolerance; undefined
  skewness (< 3 rows / zero variance) is ``not_computed`` + reason, never a
  silent-null MetricValue.
- Distribution.outlier_candidates — IQR/1.5 fences (``iqr_1_5``) counted against
  a hand-computed Tukey fence, with ``sample_row_refs`` carrying row *references*
  (never raw values, SECURITY §4).
- Temporal — range (TZ-safe CAST to VARCHAR), volume_per_granularity (auto
  granularity from the span), gaps (expected-minus-observed period series), the
  honestly ``not_computed`` ``out_of_expected_range`` (no MVP config bound), and
  ``timezone_source`` (naive/utc).
- Section gating (§4 gate 2): ``distributions`` / ``temporal`` disabled ⇒ the
  whole block is absent, never fabricated.
- QUICK mode approximate labels (execute exact SQL, keep the planner's label);
  a ``sampled`` temporal-gaps decision runs the shared bounded sample + seed.
- §6.4 partial failure isolates a broken metric from its siblings.
- Every emitted ColumnProfile validates against the frozen §3.2 schema and a
  rerun is byte-stable.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import duckdb
import polars as pl
import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery import discover
from data_profiling_engine.engine.metrics import MetricRegistry, default_registry
from data_profiling_engine.engine.metrics.profiler import (
    HISTOGRAM_BIN_COUNT,
    OUTLIER_IQR_MULTIPLIER,
    execute_distribution_and_temporal,
)
from data_profiling_engine.engine.planner import plan_execution
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.schemas.models import (
    DatasetConfig,
    MetricMethod,
    ProfileMode,
    SourceSnapshot,
)

# ---------------------------------------------------------------------------
# independent references (plain Python — NOT the engine's SQL)
# ---------------------------------------------------------------------------


def _quantile_cont(values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (DuckDB ``quantile_cont`` / R type 7)."""
    xs = sorted(values)
    n = len(xs)
    pos = q * (n - 1)
    base = math.floor(pos)
    frac = pos - base
    if base + 1 < n:
        return xs[base] + frac * (xs[base + 1] - xs[base])
    return float(xs[base])


def _skew_g1(values: Sequence[float]) -> Optional[float]:
    """Adjusted Fisher–Pearson skewness coefficient G1 — exactly what DuckDB's
    ``skewness()`` returns (Polars ``.skew()`` reports the *biased* g1 instead;
    STATE/testing note). Returns None when undefined (n < 3 or zero variance)."""
    xs = [float(v) for v in values]
    n = len(xs)
    if n < 3:
        return None
    mean = sum(xs) / n
    m2 = sum((x - mean) ** 2 for x in xs) / n
    m3 = sum((x - mean) ** 3 for x in xs) / n
    if m2 == 0:
        return None
    g1 = m3 / m2 ** 1.5
    return g1 * math.sqrt(n * (n - 1)) / (n - 2)


def _iqr_outliers(values: Sequence[float]) -> Tuple[float, float, int]:
    """Tukey fences at k=1.5 and the count of non-null values beyond them."""
    xs = [float(v) for v in values]
    q1 = _quantile_cont(xs, 0.25)
    q3 = _quantile_cont(xs, 0.75)
    iqr = q3 - q1
    lower = q1 - OUTLIER_IQR_MULTIPLIER * iqr
    upper = q3 + OUTLIER_IQR_MULTIPLIER * iqr
    count = sum(1 for x in xs if x < lower or x > upper)
    return lower, upper, count


# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------

_AMOUNT_ROWS: List[Optional[int]] = [10, 20, 30, 40, 50, 1000, None]  # 6 non-null

_TS_ROWS = [
    "2024-01-05 10:00:00",
    "2024-01-20 09:00:00",
    "2024-03-11 09:00:00",
    "2024-03-20 12:00:00",
    "2024-03-25 08:00:00",
    None,
]  # spans Jan..Mar 2024, February empty → 1 gap at month granularity


def _snapshot() -> SourceSnapshot:
    return SourceSnapshot(content_hash="sha256:" + "1" * 64)


def _num_view(
    con: duckdb.DuckDBPyConnection, view: str = "srcn",
    rows: Optional[List[Optional[int]]] = None, empty: bool = False,
    constant: Optional[int] = None,
) -> RelationHandle:
    if empty:
        con.execute(
            f'CREATE OR REPLACE VIEW "{view}" AS SELECT CAST(v0 AS BIGINT) AS amount, '
            "row_number() OVER () AS _row_id FROM (VALUES (1)) t(v0) WHERE false"
        )
    elif constant is not None:
        body = ",".join(f"({constant})" for _ in range(5))
        con.execute(
            f'CREATE OR REPLACE VIEW "{view}" AS SELECT CAST(v0 AS BIGINT) AS amount, '
            f"row_number() OVER () AS _row_id FROM (VALUES {body}) t(v0)"
        )
    else:
        rows = rows if rows is not None else _AMOUNT_ROWS
        body = ",".join("(NULL)" if r is None else f"({r})" for r in rows)
        con.execute(
            f'CREATE OR REPLACE VIEW "{view}" AS SELECT CAST(v0 AS BIGINT) AS amount, '
            f"row_number() OVER () AS _row_id FROM (VALUES {body}) t(v0)"
        )
    return RelationHandle(
        dataset_id="ds", connector="manual", view_name=view, snapshot=_snapshot(),
        columns=(("amount", "BIGINT"),), row_id_column="_row_id",
    )


def _ts_view(
    con: duckdb.DuckDBPyConnection, view: str = "srct", col: str = "created_at",
    phys: str = "TIMESTAMP", rows: Optional[List[Optional[str]]] = None,
) -> RelationHandle:
    rows = rows if rows is not None else _TS_ROWS
    body = ",".join("(NULL)" if r is None else f"(CAST('{r}' AS {phys}))" for r in rows)
    con.execute(
        f'CREATE OR REPLACE VIEW "{view}" AS SELECT v0 AS {col}, '
        f"row_number() OVER () AS _row_id FROM (VALUES {body}) t(v0)"
    )
    return RelationHandle(
        dataset_id="ds", connector="manual", view_name=view, snapshot=_snapshot(),
        columns=((col, phys),), row_id_column="_row_id",
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
        "quality_checks": [{"id": "r", "rule": "not_null", "column": "amount",
                            "dimension": "completeness", "severity": "high"}],
    })


def _run(con, h, cfg, mode=ProfileMode.STANDARD, n_rows=7, registry=None):
    report = discover(con, h, cfg)
    plan = plan_execution(
        report, mode, n_rows,
        cfg.profiling.sampling if cfg else None,
        cfg.profiling.sections if cfg else None,
        registry or default_registry(),
    )
    return execute_distribution_and_temporal(con, h, report, plan, cfg, registry)


def _by_name(profiles, name):
    for p in profiles:
        if p.column == name:
            return p
    raise AssertionError(f"column {name!r} missing")


# ---------------------------------------------------------------------------
# Distribution — histogram
# ---------------------------------------------------------------------------


class TestHistogram:
    def _con(self):
        con = duckdb.connect()
        h = _num_view(con)
        return con, h

    def test_bin_count_and_sum_invariant(self):
        con, h = self._con()
        dist = _by_name(_run(con, h, _config()).profiles, "amount").distribution
        assert dist.histogram is not None
        assert len(dist.histogram.bins) == HISTOGRAM_BIN_COUNT
        assert len(dist.histogram.counts) == HISTOGRAM_BIN_COUNT
        non_null = sum(1 for r in _AMOUNT_ROWS if r is not None)
        assert sum(dist.histogram.counts) == non_null
        con.close()

    def test_edges_span_min_max_and_strategy_recorded(self):
        con, h = self._con()
        hist = _by_name(_run(con, h, _config()).profiles, "amount").distribution.histogram
        assert float(hist.provenance.method_params["lower_edge"]) == min(
            r for r in _AMOUNT_ROWS if r is not None)
        assert float(hist.provenance.method_params["upper_edge"]) == max(
            r for r in _AMOUNT_ROWS if r is not None)
        assert hist.provenance.method_params["bin_strategy"] == "fixed_width"
        # bins are the left edges of an evenly-spaced range
        assert hist.bins[0] == pytest.approx(10.0)
        assert hist.bins[-1] < 1000.0
        con.close()

    def test_outlier_value_lands_in_last_bin(self):
        con, h = self._con()
        hist = _by_name(_run(con, h, _config()).profiles, "amount").distribution.histogram
        # 1000 == upper edge folds into the final bin; 10..50 in the first
        assert hist.counts[0] == 5
        assert hist.counts[-1] == 1
        con.close()

    def test_constant_column_single_bin(self):
        con = duckdb.connect()
        h = _num_view(con, constant=7)
        hist = _by_name(_run(con, h, _config(), n_rows=5).profiles, "amount").distribution.histogram
        assert hist.bins == [7.0]
        assert hist.counts == [5]
        con.close()

    def test_empty_column_honest_not_computed(self):
        con = duckdb.connect()
        h = _num_view(con, empty=True)
        dist = _by_name(_run(con, h, _config(), n_rows=0).profiles, "amount").distribution
        assert dist.histogram.bins == [] and dist.histogram.counts == []
        assert dist.histogram.provenance.method == MetricMethod.NOT_COMPUTED.value
        assert dist.histogram.provenance.reason
        con.close()

    def test_histogram_polars_independent_sum(self):
        """Cross-check the non-null total against Polars (drop_nulls length)."""
        con, h = self._con()
        hist = _by_name(_run(con, h, _config()).profiles, "amount").distribution.histogram
        s = pl.Series("amount", _AMOUNT_ROWS).drop_nulls()
        assert sum(hist.counts) == len(s) == 6
        con.close()


# ---------------------------------------------------------------------------
# Distribution — skewness
# ---------------------------------------------------------------------------


class TestSkewness:
    def test_matches_adjusted_fisher_pearson_g1(self):
        con = duckdb.connect()
        h = _num_view(con)
        mv = _by_name(_run(con, h, _config()).profiles, "amount").distribution.skewness
        xs = [float(r) for r in _AMOUNT_ROWS if r is not None]
        assert mv.value == pytest.approx(_skew_g1(xs), abs=1e-9)
        assert mv.provenance.method == MetricMethod.EXACT.value
        con.close()

    def test_differs_from_polars_biased_g1(self):
        """DuckDB reports G1 (adjusted), Polars .skew() reports g1 (biased) — a
        documented difference; the engine must follow DuckDB G1, not Polars."""
        con = duckdb.connect()
        h = _num_view(con)
        mv = _by_name(_run(con, h, _config()).profiles, "amount").distribution.skewness
        s = pl.Series("amount", [r for r in _AMOUNT_ROWS if r is not None]).cast(pl.Float64)
        assert mv.value != pytest.approx(float(s.skew()), abs=1e-6)
        assert mv.value == pytest.approx(
            float(s.skew()) * math.sqrt(6 * 5) / 4, abs=1e-6)
        con.close()

    def test_constant_column_skewness_not_computed(self):
        con = duckdb.connect()
        h = _num_view(con, constant=9)
        mv = _by_name(_run(con, h, _config(), n_rows=5).profiles, "amount").distribution.skewness
        # undefined (zero variance) ⇒ honest not_computed, never a silent null
        assert mv.provenance.method == MetricMethod.NOT_COMPUTED.value
        assert "value" not in mv.model_dump()
        con.close()


# ---------------------------------------------------------------------------
# Distribution — outlier candidates
# ---------------------------------------------------------------------------


class TestOutlierCandidates:
    def test_iqr_count_matches_hand_reference(self):
        con = duckdb.connect()
        h = _num_view(con)
        out = _by_name(_run(con, h, _config()).profiles, "amount").distribution.outlier_candidates
        _, _, expected = _iqr_outliers([float(r) for r in _AMOUNT_ROWS if r is not None])
        assert out.count == expected == 1
        assert out.method == "iqr_1_5"
        assert out.provenance.method_params["iqr_multiplier"] == OUTLIER_IQR_MULTIPLIER
        con.close()

    def test_sample_row_refs_are_references_not_values(self):
        con = duckdb.connect()
        h = _num_view(con)
        out = _by_name(_run(con, h, _config()).profiles, "amount").distribution.outlier_candidates
        # 1000 is row #6 (1-based); refs carry the row id, never the raw value
        assert out.sample_row_refs == ["row:6"]
        assert all(r.startswith("row:") for r in out.sample_row_refs)
        con.close()

    def test_no_outliers_when_tight_spread(self):
        con = duckdb.connect()
        h = _num_view(con, rows=[1, 2, 3, 4, 5, 6])
        out = _by_name(_run(con, h, _config(), n_rows=6).profiles, "amount").distribution.outlier_candidates
        assert out.count == 0
        assert out.sample_row_refs is None  # omitted, never an empty fabrication
        con.close()

    def test_seeded_iqr_fixture(self):
        """A hand-crafted fixture with a known outlier count (deterministic)."""
        con = duckdb.connect()
        h = _num_view(con, rows=[10, 11, 12, 13, 14, 15, 100, -80])
        _, _, expected = _iqr_outliers([10, 11, 12, 13, 14, 15, 100, -80])
        out = _by_name(_run(con, h, _config(), n_rows=8).profiles, "amount").distribution.outlier_candidates
        assert out.count == expected == 2  # 100 and -80 both beyond the fences
        con.close()


# ---------------------------------------------------------------------------
# Temporal — range / volume / gaps / timezone / out-of-range
# ---------------------------------------------------------------------------


class TestTemporal:
    def test_range_is_cast_varchar(self):
        con = duckdb.connect()
        h = _ts_view(con)
        temporal = _by_name(_run(con, h, _config(), n_rows=6).profiles, "created_at").temporal
        assert temporal.range.min == "2024-01-05 10:00:00"
        assert temporal.range.max == "2024-03-25 08:00:00"
        con.close()

    def test_granularity_auto_from_span(self):
        con = duckdb.connect()
        h = _ts_view(con)
        vol = _by_name(_run(con, h, _config(), n_rows=6).profiles, "created_at").temporal.volume_per_granularity
        # ~80-day span → month granularity
        assert vol["granularity"] == "month"
        buckets = {b["period"]: b["count"] for b in vol["buckets"]}
        assert buckets == {"2024-01-01 00:00:00": 2, "2024-03-01 00:00:00": 3}
        con.close()

    def test_gaps_detect_missing_month(self):
        con = duckdb.connect()
        h = _ts_view(con)
        gaps = _by_name(_run(con, h, _config(), n_rows=6).profiles, "created_at").temporal.gaps
        assert gaps["count"] == 1
        assert gaps["missing_periods"] == ["2024-02-01 00:00:00"]
        assert gaps["provenance"]["method"] == MetricMethod.EXACT.value
        con.close()

    def test_out_of_expected_range_honest_not_computed(self):
        con = duckdb.connect()
        h = _ts_view(con)
        oob = _by_name(_run(con, h, _config(), n_rows=6).profiles, "created_at").temporal.out_of_expected_range
        assert oob["provenance"]["method"] == MetricMethod.NOT_COMPUTED.value
        assert "declared" in oob["provenance"]["reason"]
        con.close()

    def test_timezone_naive_vs_utc(self):
        con = duckdb.connect()
        naive = _by_name(_run(con, _ts_view(con, view="tn"), _config(), n_rows=6).profiles,
                         "created_at").temporal
        assert naive.timezone_source == "naive"
        tz = _by_name(_run(con, _ts_view(con, view="ttz", phys="TIMESTAMP WITH TIME ZONE",
                                         rows=["2024-01-05 10:00:00+00", "2024-03-11 09:00:00+00"]),
                           _config(), n_rows=2).profiles, "created_at").temporal
        assert tz.timezone_source == "utc"
        con.close()

    def test_day_granularity_short_span(self):
        con = duckdb.connect()
        rows = ["2024-05-01 08:00:00", "2024-05-03 08:00:00"]  # 2-day span → day
        h = _ts_view(con, rows=rows)
        temporal = _by_name(_run(con, h, _config(), n_rows=2).profiles, "created_at").temporal
        assert temporal.volume_per_granularity["granularity"] == "day"
        # May 2 is the single missing day between the two observed days
        assert temporal.gaps["missing_periods"] == ["2024-05-02 00:00:00"]
        con.close()


# ---------------------------------------------------------------------------
# Section gating (§4 gate 2) — block absent, never fabricated
# ---------------------------------------------------------------------------


class TestSectionGating:
    def _mixed(self, con):
        con.execute(
            'CREATE OR REPLACE VIEW "srcm" AS SELECT CAST(v0 AS BIGINT) AS amount, '
            "CAST(v1 AS TIMESTAMP) AS created_at, row_number() OVER () AS _row_id "
            "FROM (VALUES (10, CAST('2024-01-05 10:00:00' AS TIMESTAMP)),"
            "(20, CAST('2024-03-11 09:00:00' AS TIMESTAMP)),"
            "(30, CAST('2024-03-20 12:00:00' AS TIMESTAMP))) t(v0,v1)"
        )
        return RelationHandle(
            dataset_id="ds", connector="manual", view_name="srcm", snapshot=_snapshot(),
            columns=(("amount", "BIGINT"), ("created_at", "TIMESTAMP")), row_id_column="_row_id",
        )

    def test_distributions_off_omits_block_keeps_temporal(self):
        con = duckdb.connect()
        h = self._mixed(con)
        res = _run(con, h, _config(sections=["schema", "temporal"]), n_rows=3)
        assert _by_name(res.profiles, "amount").distribution is None
        assert _by_name(res.profiles, "created_at").temporal is not None
        con.close()

    def test_temporal_off_omits_block_keeps_distribution(self):
        con = duckdb.connect()
        h = self._mixed(con)
        res = _run(con, h, _config(sections=["schema", "distributions"]), n_rows=3)
        assert _by_name(res.profiles, "amount").distribution is not None
        assert _by_name(res.profiles, "created_at").temporal is None
        con.close()


# ---------------------------------------------------------------------------
# Quick approximate label + sampled gaps
# ---------------------------------------------------------------------------


class TestMethodLabels:
    def test_quick_mode_labels_approximate_but_executes_exact(self):
        con = duckdb.connect()
        h = _num_view(con)
        dist = _by_name(_run(con, h, _config("quick"), mode=ProfileMode.QUICK).profiles,
                        "amount").distribution
        # planner degraded each moderate_exact metric to `approximate`; the
        # runner executed the exact SQL but kept the label + algorithm (§2.2)
        assert dist.histogram.provenance.method == MetricMethod.APPROXIMATE.value
        assert dist.histogram.provenance.method_params["algorithm"] == "histogram"
        assert dist.skewness.provenance.method == MetricMethod.APPROXIMATE.value
        # value still equals the exact reference (we under-claim, never over-)
        xs = [float(r) for r in _AMOUNT_ROWS if r is not None]
        assert dist.skewness.value == pytest.approx(_skew_g1(xs), abs=1e-9)
        con.close()

    def test_sampled_gaps_run_on_bounded_sample_with_seed(self):
        con = duckdb.connect()
        h = _ts_view(con)
        cfg = _config(sampling={"enabled": True, "strategy": "reservoir", "size": 4, "seed": 20261024})
        # EXPENSIVE_EXACT gaps + sampling in standard → SAMPLED
        temporal = _by_name(_run(con, h, cfg, n_rows=6).profiles, "created_at").temporal
        assert temporal.gaps["provenance"]["method"] == MetricMethod.SAMPLED.value
        assert temporal.gaps["provenance"]["method_params"]["seed"] == 20261024
        con.close()

    def test_sampled_is_deterministic_same_seed(self):
        vals = []
        for _ in range(2):
            con = duckdb.connect()
            h = _ts_view(con)
            cfg = _config(sampling={"enabled": True, "strategy": "reservoir", "size": 4, "seed": 7})
            res = _run(con, h, cfg, n_rows=6)
            vals.append(_by_name(res.profiles, "created_at").temporal.gaps)
            con.close()
        assert vals[0] == vals[1]


# ---------------------------------------------------------------------------
# §6.4 partial failure
# ---------------------------------------------------------------------------


class TestPartialFailure:
    def test_broken_skewness_isolated_from_siblings(self):
        con = duckdb.connect()
        h = _num_view(con)
        base = next(s for s in default_registry().specs if s.metric_id == "distribution.skewness")
        broken = type(base)(
            metric_id="distribution.skewness", family="distribution",
            display_name="skewness (broken)",
            contract_target="column_profile.distribution.skewness",
            scope=base.scope, cost_class=base.cost_class,
            applicable_logical=base.applicable_logical, section="distributions",
            supports_approximate=False,
            sql_template="SELECT no_such_fn({col}) FROM {table}",
        )
        reg = MetricRegistry([s for s in default_registry().specs
                              if s.metric_id != "distribution.skewness"] + [broken])
        res = _run(con, h, _config(), registry=reg)
        dist = _by_name(res.profiles, "amount").distribution
        # skewness errored → not_computed + reason; histogram/outlier still ran
        assert dist.skewness.provenance.method == MetricMethod.NOT_COMPUTED.value
        assert dist.skewness.provenance.reason
        assert dist.histogram is not None and sum(dist.histogram.counts) == 6
        assert any(e.task and e.task.startswith("distribution.skewness:amount") for e in res.errors)
        assert any(e.code == ErrorCode.METRIC_NOT_COMPUTED.value for e in res.errors)
        con.close()


# ---------------------------------------------------------------------------
# Edges + contract conformance + determinism
# ---------------------------------------------------------------------------


class TestEdgesAndConformance:
    def test_every_profile_schema_valid_all_modes(self):
        con = duckdb.connect()
        h = _num_view(con)
        ht = _ts_view(con, view="srct2")
        for mode in (ProfileMode.STANDARD, ProfileMode.QUICK, ProfileMode.DEEP):
            for hh, n in ((h, 7), (ht, 6)):
                res = _run(con, hh, _config(mode.value), mode=mode, n_rows=n)
                for p in res.profiles:
                    errs = S.validate("column_profile.schema.json", p.model_dump(mode="json"))
                    assert errs == [], (mode, p.column, errs)
        con.close()

    def test_identical_rerun_byte_stable(self):
        con = duckdb.connect()
        h = _num_view(con)
        a = _run(con, h, _config()).profiles
        b = _run(con, h, _config()).profiles
        assert [p.model_dump_json() for p in a] == [p.model_dump_json() for p in b]
        con.close()

    def test_numeric_column_has_no_temporal_and_vice_versa(self):
        con = duckdb.connect()
        num = _by_name(_run(con, _num_view(con), _config()).profiles, "amount")
        assert num.temporal is None and num.distribution is not None
        ts = _by_name(_run(con, _ts_view(con, view="srcx"), _config(), n_rows=6).profiles, "created_at")
        assert ts.distribution is None and ts.temporal is not None
        con.close()
