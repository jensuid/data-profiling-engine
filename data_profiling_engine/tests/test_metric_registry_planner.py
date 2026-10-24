"""M2-T3 reference tests — metric registry + execution planner.

TESTING §3.1 (independent-reference obligation) applied to the planning
surface: the §2.1 cost-class table and §8 mode matrix are behavioural
contracts, so they are tested table-driven against an *independent*
implementation of the decision policy (a plain dict literal transcribed
from the spec prose), not against the planner's own code. Registry rows
are checked against ENGINE §3's profiler table (families/provenance) and
resolved against the frozen contract models. Templated SQL is executed on
tiny hand-computed DuckDB fixtures — expected values are literals here,
derived independently of the engine.
"""
from __future__ import annotations

from typing import List, Optional

import duckdb
import pytest

from data_profiling_engine.schemas import validator_for
from data_profiling_engine.discovery.types import (
    ColumnDiscovery,
    ColumnSignals,
    Confidence,
    DiscoveryReport,
    LogicalType,
)
from data_profiling_engine.engine.metrics import (
    LARGE_ROW_LIMIT,
    CostClass,
    MetricRegistry,
    MetricRegistryError,
    MetricScope,
    MetricSpec,
    default_registry,
)
from data_profiling_engine.engine.planner import (
    PlanDecision,
    PlanningError,
    decide_method,
    plan_execution,
    render_sql,
)
from data_profiling_engine.schemas.models import (
    ColumnProfile,
    ConfigSampling,
    MetricMethod,
    ProfileMode,
    Provenance,
    SemanticType,
)

MODES = list(ProfileMode)
SMALL = 100
LARGE = LARGE_ROW_LIMIT + 1


def _col(
    name: str = "c",
    logical: LogicalType = LogicalType.INTEGER,
    physical: str = "BIGINT",
    semantic: SemanticType = SemanticType.MEASURE,
) -> ColumnDiscovery:
    profile = ColumnProfile(
        schema_version="1.0",
        column=name,
        ordinal=0,
        physical_type=physical,
        logical_type=logical.value,
        semantic_type=semantic.value,
    )
    signals = ColumnSignals(n_rows=SMALL, n_null=0, n_notnull=SMALL, n_distinct=50)
    return ColumnDiscovery(
        profile=profile, logical=logical, confidence=Confidence.HIGH,
        rationale="test", signals=signals,
    )


def _report(cols: List[ColumnDiscovery]) -> DiscoveryReport:
    return DiscoveryReport(dataset_id="ds-test", columns=cols)


def _sampling(size: int = 1000, seed: int = 20261024) -> ConfigSampling:
    return ConfigSampling(enabled=True, strategy="reservoir", size=size, seed=seed)


def _schema_paths(node: dict, root: dict, prefix: str) -> set:
    """Recursively enumerate every dotted property path of a JSON Schema
    object ($ref-resolved against the same document's $defs)."""
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        node = root["$defs"][name]
    out = set()
    for prop, sub in (node.get("properties") or {}).items():
        path = f"{prefix}{prop}"
        out.add(path)
        target = sub if "properties" in sub else next(
            (s for s in sub.get("anyOf", []) if isinstance(s, dict) and "properties" in s), None
        )
        if target is not None:
            out |= _schema_paths(target, root, f"{path}.")
    return out


def _decide(metric_id: str, mode: ProfileMode, n_rows: int,
            sampling: Optional[ConfigSampling] = None) -> MetricMethod:
    method, _, _ = decide_method(default_registry().get(metric_id), mode, n_rows, sampling)
    return method


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------


class TestRegistryIntegrity:
    def test_metric_ids_unique(self):
        ids = [s.metric_id for s in default_registry().specs]
        assert len(ids) == len(set(ids))

    def test_duplicate_id_rejected(self):
        spec = default_registry().specs[0]
        with pytest.raises(MetricRegistryError, match="duplicate"):
            MetricRegistry([spec, spec])

    def test_non_mvp_row_rejected(self):
        spec = MetricSpec(
            "relationship.correlation", "relationship", "correlation", None,
            MetricScope.COLUMN, CostClass.EXPENSIVE_EXACT, mvp_active=False,
        )
        with pytest.raises(MetricRegistryError, match="non-MVP"):
            MetricRegistry([spec])

    def test_unknown_metric_id_raises(self):
        with pytest.raises(MetricRegistryError, match="unknown"):
            default_registry().get("stats.dne")

    def test_contract_targets_resolve_on_frozen_models(self):
        """Every declared contract_target is a real path through the frozen
        contract schemas — a typo here would plan metrics that can never be
        persisted contract-valid. Paths are enumerated from the JSON Schema
        itself ($ref-resolved), the M1-frozen source of truth."""
        raw_col = validator_for("column_profile.schema.json").schema
        raw_ds = validator_for("dataset_profile.schema.json").schema
        col_paths = _schema_paths(raw_col, raw_col, "")
        ds_paths = _schema_paths(raw_ds, raw_ds, "")
        valid = {f"column_profile.{p}" for p in col_paths} | {f"dataset_profile.{p}" for p in ds_paths}
        for spec in default_registry().specs:
            if spec.contract_target is not None:
                assert spec.contract_target in valid, spec.metric_id

    def test_contract_targets_are_column_or_dataset_profile(self):
        for spec in default_registry().specs:
            if spec.contract_target is not None:
                assert spec.contract_target.split(".")[0] in ("column_profile", "dataset_profile")

    def test_applicability_uses_closed_logical_enum(self):
        for spec in default_registry().specs:
            for lt in spec.applicable_logical:
                assert isinstance(lt, LogicalType)

    def test_dataset_scope_rows_carry_no_column_applicability(self):
        for spec in default_registry().dataset_metrics():
            assert not spec.applicable_logical


# ---------------------------------------------------------------------------
# Catalog ↔ ENGINE §3 profiler table parity
# ---------------------------------------------------------------------------


class TestCatalogParity:
    EXPECTED_FAMILIES = {
        "schema", "statistical", "categorical", "missingness",
        "uniqueness", "distribution", "temporal", "pattern",
    }

    def test_families_match_mvp_active_profilers(self):
        """§3 marks relationship/relational/drift Phase-2/Phase-4 — they must
        not appear; every yes/partial profiler has at least one row."""
        families = {s.family for s in default_registry().specs}
        assert families == self.EXPECTED_FAMILIES

    def test_cost_classes_match_spec_2_1_examples(self):
        """The §2.1 table names concrete examples per class; the registry must
        classify those exact metrics the same way."""
        reg = default_registry()
        # §2.1 cheap_exact examples: count, null count, min/max, mean
        assert reg.get("stats.valid_count").cost_class is CostClass.CHEAP_EXACT
        assert reg.get("missingness.null_count").cost_class is CostClass.CHEAP_EXACT
        assert reg.get("stats.min").cost_class is CostClass.CHEAP_EXACT
        assert reg.get("stats.mean").cost_class is CostClass.CHEAP_EXACT
        # §2.1 moderate_exact: stddev, quantile_cont
        assert reg.get("stats.stddev").cost_class is CostClass.MODERATE_EXACT
        assert reg.get("stats.median").cost_class is CostClass.MODERATE_EXACT
        # §2.1 expensive_exact: COUNT(DISTINCT) on high cardinality, duplicate grouping
        assert reg.get("uniqueness.distinct_count").cost_class is CostClass.EXPENSIVE_EXACT
        assert reg.get("uniqueness.duplicate_groups").cost_class is CostClass.EXPENSIVE_EXACT
        # §2.1 prohibited_at_scale: full-column heavy scans, sample/cap with labeling
        assert reg.get("pattern.text_format").cost_class is CostClass.PROHIBITED_AT_SCALE

    def test_hll_approximate_template_present_for_distinct(self):
        """§2.2 distinctness approximation is approx_count_distinct (HLL)."""
        spec = default_registry().get("uniqueness.distinct_count")
        assert "approx_count_distinct" in spec.approx_sql_template

    def test_tz_temporal_metrics_declare_cast_on_fetch(self):
        """Gotcha: no pytz shipped — temporal scalars must CAST on fetch."""
        for mid in ("temporal.range_min", "temporal.range_max"):
            assert default_registry().get(mid).cast_varchar_on_fetch


# ---------------------------------------------------------------------------
# §2.1 / §8 decision table — independent reference
# ---------------------------------------------------------------------------

# (cost_class, mode, large, sampling_enabled) -> expected method for a metric
# that supports approximate and has NO approx template (so expensive falls
# through per the reference policy transcribed from the spec prose).
REFERENCE_TABLE = {
    (CostClass.CHEAP_EXACT, ProfileMode.QUICK, False, False): MetricMethod.EXACT,
    (CostClass.CHEAP_EXACT, ProfileMode.STANDARD, True, False): MetricMethod.EXACT,
    (CostClass.CHEAP_EXACT, ProfileMode.DEEP, True, False): MetricMethod.EXACT,
    (CostClass.MODERATE_EXACT, ProfileMode.STANDARD, False, False): MetricMethod.EXACT,
    (CostClass.MODERATE_EXACT, ProfileMode.DEEP, True, False): MetricMethod.EXACT,
    (CostClass.MODERATE_EXACT, ProfileMode.QUICK, False, False): MetricMethod.APPROXIMATE,
    (CostClass.MODERATE_EXACT, ProfileMode.QUICK, True, False): MetricMethod.APPROXIMATE,
    (CostClass.EXPENSIVE_EXACT, ProfileMode.DEEP, True, False): MetricMethod.EXACT,
    (CostClass.EXPENSIVE_EXACT, ProfileMode.STANDARD, True, False): MetricMethod.APPROXIMATE,
    (CostClass.EXPENSIVE_EXACT, ProfileMode.STANDARD, False, False): MetricMethod.EXACT,
    (CostClass.EXPENSIVE_EXACT, ProfileMode.QUICK, False, False): MetricMethod.APPROXIMATE,
}


def _synthetic(cost: CostClass, *, approx: bool = True, full: bool = False,
               exact_us: bool = False) -> MetricSpec:
    return MetricSpec(
        f"synthetic.{cost.value}.{int(full)}", "statistical", "test probe", None,
        MetricScope.COLUMN, cost, supports_approximate=approx,
        requires_full_relation=full, exact_under_sampling=exact_us,
    )


class TestDecisionTable:
    @pytest.mark.parametrize(
        "cost, mode, large, sampling, expected",
        [(*k, v) for k, v in REFERENCE_TABLE.items()],
    )
    def test_reference_table(self, cost, mode, large, sampling, expected):
        n = LARGE if large else SMALL
        s = _sampling() if sampling else None
        spec = _synthetic(cost, exact_us=True)
        method, _, _ = decide_method(spec, mode, n, s)
        assert method is expected

    def test_quick_degrades_moderate_to_sketch(self):
        """§8 quick: 'heavy approximate/sampled, all labeled'."""
        method, params, _ = decide_method(
            default_registry().get("stats.median"), ProfileMode.QUICK, SMALL
        )
        assert method is MetricMethod.APPROXIMATE
        assert params.get("algorithm")

    def test_expensive_large_uses_hll_algorithm(self):
        method, params, _ = decide_method(
            default_registry().get("uniqueness.distinct_count"), ProfileMode.STANDARD, LARGE
        )
        assert method is MetricMethod.APPROXIMATE
        assert params["algorithm"] == "HLL"

    def test_expensive_small_standard_stays_exact(self):
        assert _decide("uniqueness.distinct_count", ProfileMode.STANDARD, SMALL) is MetricMethod.EXACT

    def test_deep_exact_overrides_large(self):
        assert _decide("uniqueness.distinct_count", ProfileMode.DEEP, LARGE) is MetricMethod.EXACT

    def test_prohibited_never_exact(self):
        spec = default_registry().get("pattern.text_format")
        for mode in MODES:
            method, _, reason = decide_method(spec, mode, LARGE)
            assert method is MetricMethod.NOT_COMPUTED
            assert reason  # §2.1 "sample/cap with labeling" — no silent skip

    def test_prohibited_sampled_when_window(self):
        method, params, _ = decide_method(
            default_registry().get("pattern.text_format"), ProfileMode.STANDARD, LARGE, _sampling()
        )
        assert method is MetricMethod.SAMPLED
        assert params["seed"] == 20261024 and params["strategy"] == "reservoir"

    def test_sampling_window_labels_non_full_exact_rows_sampled(self):
        """§2.2 honesty: a metric executed inside the sampling window carries
        method=sampled + seed, never a borrowed exact label."""
        spec = _synthetic(CostClass.CHEAP_EXACT, approx=False, exact_us=False)
        method, params, _ = decide_method(spec, ProfileMode.STANDARD, LARGE, _sampling())
        assert method is MetricMethod.SAMPLED
        assert params["seed"] == 20261024

    def test_cheap_exact_survives_sampling(self):
        """§2.1 cheap_exact 'exact always' — a window never relabels it."""
        spec = _synthetic(CostClass.CHEAP_EXACT, approx=False, exact_us=True)
        method, _, _ = decide_method(spec, ProfileMode.STANDARD, LARGE, _sampling())
        assert method is MetricMethod.EXACT

    def test_full_relation_metric_refuses_sampled_deep(self):
        spec = _synthetic(CostClass.EXPENSIVE_EXACT, approx=False, full=True)
        method, _, reason = decide_method(spec, ProfileMode.DEEP, LARGE, _sampling())
        assert method is MetricMethod.NOT_COMPUTED
        assert "full relation" in reason

    def test_moderate_quick_falls_back_to_sampled_without_sketch(self):
        spec = _synthetic(CostClass.MODERATE_EXACT, approx=False)
        method, params, _ = decide_method(spec, ProfileMode.QUICK, SMALL, _sampling())
        assert method is MetricMethod.SAMPLED and params["seed"]

    def test_moderate_quick_not_computed_without_window(self):
        spec = _synthetic(CostClass.MODERATE_EXACT, approx=False)
        method, _, reason = decide_method(spec, ProfileMode.QUICK, SMALL)
        assert method is MetricMethod.NOT_COMPUTED and reason


class TestDecisionInvariants:
    def test_sampled_requires_seed(self):
        with pytest.raises(PlanningError, match="seed"):
            PlanDecision("x", "c", MetricMethod.SAMPLED, params={"strategy": "reservoir"})

    def test_approximate_requires_algorithm(self):
        with pytest.raises(PlanningError, match="algorithm"):
            PlanDecision("x", "c", MetricMethod.APPROXIMATE)

    def test_not_computed_requires_reason(self):
        with pytest.raises(PlanningError, match="reason"):
            PlanDecision("x", "c", MetricMethod.NOT_COMPUTED)

    def test_provenance_kwargs_feed_contract_model(self):
        """A decision must be directly stampable into contract Provenance
        (ADR-015) — method_params present only when non-empty (never a
        silent null on the contract surface)."""
        d = PlanDecision("stats.mean", "amount", MetricMethod.EXACT)
        p = Provenance(**d.provenance_kwargs())
        assert p.method == MetricMethod.EXACT.value
        assert "method_params" not in p.model_dump()
        d2 = PlanDecision("uniqueness.distinct_count", "id", MetricMethod.APPROXIMATE,
                          params={"algorithm": "HLL"})
        p2 = Provenance(**d2.provenance_kwargs())
        assert p2.method_params == {"algorithm": "HLL"}


# ---------------------------------------------------------------------------
# plan_execution — selection + section gating (ENGINE §4)
# ---------------------------------------------------------------------------


class TestPlanExecution:
    def test_numeric_column_gets_numeric_metrics(self):
        plan = plan_execution(_report([_col("amount")]), ProfileMode.STANDARD, SMALL,
                              registry=default_registry())
        ids = {d.metric_id for cp in plan.column_plans for d in cp.decisions}
        assert "stats.mean" in ids and "temporal.range_min" not in ids
        assert "missingness.blank_count" not in ids  # physical gate: BIGINT column

    def test_varchar_categorical_column_selection(self):
        col = _col("stage", logical=LogicalType.CATEGORICAL_NOMINAL,
                   physical="VARCHAR", semantic=SemanticType.STATUS)
        plan = plan_execution(_report([col]), ProfileMode.STANDARD, SMALL,
                              registry=default_registry())
        ids = {d.metric_id for d in plan.column_plans[0].decisions}
        assert {"categorical.frequencies", "missingness.blank_count",
                "pattern.text_format", "uniqueness.distinct_count"} <= ids
        assert "stats.mean" not in ids

    def test_sections_gate_emits_not_computed(self):
        """§4 gate 2: a disabled section's metrics become not_computed +
        reason — the contract row still gets emitted, never silently absent."""
        cols = [_col("amount"), _col("stage", logical=LogicalType.CATEGORICAL_NOMINAL,
                       physical="VARCHAR", semantic=SemanticType.STATUS)]
        sections = ["schema", "descriptive_statistics", "missing_values", "uniqueness"]
        plan = plan_execution(_report(cols), ProfileMode.STANDARD, SMALL,
                              sections=sections, registry=default_registry())
        by_id = {d.metric_id: d for cp in plan.column_plans for d in cp.decisions}
        assert by_id["stats.mean"].method is MetricMethod.EXACT
        dropped = [mid for mid in by_id if mid.startswith(("categorical.", "distribution.", "temporal.", "pattern."))]
        assert dropped, "expected section-gated metrics in plan"
        for mid in dropped:
            assert by_id[mid].method is MetricMethod.NOT_COMPUTED
            assert "disabled by dataset config" in by_id[mid].reason

    def test_no_sections_means_all_active(self):
        plan = plan_execution(_report([_col("amount")]), ProfileMode.STANDARD, SMALL,
                              registry=default_registry())
        assert all(d.metric_id != "stats.mean" or d.method is MetricMethod.EXACT
                   for d in plan.computed_tasks())
        assert not any("disabled by dataset config" in (d.reason or "") for d in plan.skipped())

    def test_plan_shape_dataset_once_per_column_sets(self):
        cols = [_col("a"), _col("b"), _col("c")]
        plan = plan_execution(_report(cols), ProfileMode.STANDARD, SMALL,
                              registry=default_registry())
        assert len(plan.column_plans) == 3
        assert {d.column for d in plan.dataset_decisions} == {None}
        assert {d.column for cp in plan.column_plans for d in cp.decisions} == {"a", "b", "c"}

    def test_computed_and_skipped_partition_all_decisions(self):
        plan = plan_execution(
            _report([_col("amount")]), ProfileMode.QUICK, LARGE,
            sections=["schema", "descriptive_statistics"], registry=default_registry(),
        )
        total = len(plan.dataset_decisions) + sum(len(cp.decisions) for cp in plan.column_plans)
        assert len(plan.computed_tasks()) + len(plan.skipped()) == total

    def test_plan_is_deterministic(self):
        cols = [_col("amount"), _col("stage", logical=LogicalType.CATEGORICAL_NOMINAL,
                       physical="VARCHAR")]
        a = plan_execution(_report(cols), ProfileMode.STANDARD, SMALL, registry=default_registry())
        b = plan_execution(_report(cols), ProfileMode.STANDARD, SMALL, registry=default_registry())
        assert a == b


# ---------------------------------------------------------------------------
# Template execution on a hand-computed fixture (independent reference)
# ---------------------------------------------------------------------------

# Fixture columns: n numeric {3,1,2,NULL}, s varchar {'a','b','',NULL}


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    # explicit column aliases so `n` really holds the numbers and `s` the
    # strings (VALUES rows are (num, str) — alias order matters)
    c.execute(
        "CREATE VIEW fx AS SELECT CAST(v0 AS BIGINT) AS n, CAST(v1 AS VARCHAR) AS s "
        "FROM (VALUES (3, 'a'), (1, 'b'), (2, ''), (NULL, NULL)) AS t(v0, v1)"
    )
    yield c
    c.close()


# Hand-computed expectations over the fx view (independent of the engine):
#   n = {3,1,2,NULL}: count 3, min 1, max 3, avg 2.0, stddev_samp 1,
#   quantile_cont over the ordered non-nulls {1,2,3}: p25 = 1.5, median = 2,
#   p75 = 2.5 (linear interpolation — computed by hand, not via DuckDB docs).
#   s = {'a','b','',NULL}: null_count 1 (blank is not null), blank_count 1.
TEMPLATED_COLUMN_METRICS = [
    ("stats.valid_count", "n", 3),
    ("stats.min", "n", 1),
    ("stats.max", "n", 3),
    ("stats.mean", "n", 2.0),
    ("stats.median", "n", 2.0),
    ("stats.p25", "n", 1.5),
    ("stats.p75", "n", 2.5),
    ("stats.stddev", "n", 1.0),   # stddev_samp{1,2,3} = 1 exactly
    ("missingness.null_count", "n", 1),
    ("missingness.null_count", "s", 1),
    ("missingness.blank_count", "s", 1),
    ("uniqueness.distinct_count", "n", 3),
]


class TestTemplateExecution:
    @pytest.mark.parametrize("metric_id,column,expected", TEMPLATED_COLUMN_METRICS)
    def test_exact_templates_hand_computed(self, con, metric_id, column, expected):
        spec = default_registry().get(metric_id)
        sql = render_sql(spec, "fx", column=column, physical_type="BIGINT")
        assert con.execute(sql).fetchone()[0] == pytest.approx(expected)

    def test_approx_distinct_within_hll_error(self, con):
        sql = render_sql(default_registry().get("uniqueness.distinct_count"), "fx",
                         column="n", method=MetricMethod.APPROXIMATE)
        assert con.execute(sql).fetchone()[0] == pytest.approx(3, abs=1)

    def test_quantile_sketch_template_executes(self, con):
        sql = render_sql(default_registry().get("stats.median"), "fx", column="n",
                         method=MetricMethod.APPROXIMATE)
        # t-digest on 3 values is not a guarantee — bounded sanity, not exactness
        assert con.execute(sql).fetchone()[0] == pytest.approx(2.0, abs=0.6)

    def test_dataset_templates(self, con):
        assert con.execute(render_sql(default_registry().get("schema.row_count"), "fx")).fetchone()[0] == 4
        assert con.execute(render_sql(default_registry().get("schema.column_count"), "fx")).fetchone()[0] == 2

    def test_render_rejects_missing_template(self, con):
        # M2-T4 gave sentinel_count/missing_rate their templates; the
        # no-template guard now rides on an still-untemplated row.
        with pytest.raises(PlanningError, match="no sql_template"):
            render_sql(default_registry().get("uniqueness.is_unique_candidate"), "fx", column="n")

    def test_render_rejects_unsubstituted_placeholder(self, con):
        # {sentinels} must be provided via params (ADR-017 binding seam)
        with pytest.raises(PlanningError, match="unsubstituted"):
            render_sql(default_registry().get("missingness.sentinel_count"), "fx", column="n")
        sql = render_sql(default_registry().get("missingness.sentinel_count"), "fx",
                         column="n", params={"sentinels": '"sent"'})
        assert "{sentinels}" not in sql and '"sent"' in sql

    def test_render_rejects_approx_without_template(self, con):
        with pytest.raises(PlanningError, match="no approx"):
            render_sql(default_registry().get("stats.mean"), "fx", column="n",
                       method=MetricMethod.APPROXIMATE)

    def test_temporal_cast_wrap(self, con):
        con.execute("CREATE OR REPLACE VIEW tzz AS SELECT TIMESTAMPTZ '2026-01-01 00:00:00Z' AS ts")
        sql = render_sql(default_registry().get("temporal.range_min"), "tzz", column="ts",
                         physical_type="TIMESTAMP WITH TIME ZONE")
        assert "CAST(" in sql and "AS VARCHAR)" in sql
        val = con.execute(sql).fetchone()[0]  # must fetch without pytz
        assert isinstance(val, str) and "2026" in val

    def test_identifier_quoting_survives_odd_names(self, con):
        # ""-doubling in the fixture DDL mirrors _q()'s escaping policy
        con.execute('CREATE VIEW "we""ird""view" AS SELECT 42 AS "col name"')
        sql = render_sql(default_registry().get("stats.valid_count"), 'we"ird"view',
                         column="col name")
        assert con.execute(sql).fetchone()[0] == 1
