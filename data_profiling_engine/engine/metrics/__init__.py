"""Metric registry core (M2-T3, ENGINE §2.1 / §3 / §4).

Metadata-driven catalog: one algorithm family, many datasets (ENGINE §1,
AGENTS §5). Each MVP-active profiler metric from ENGINE §3's table is a
:class:`MetricSpec` row declaring *what* it computes, *where* it lands in the
frozen contracts, *when* it applies (logical/semantic/physical selection,
ENGINE §4 gates 1–2) and *how expensive* it is (:class:`CostClass`, §2.1).
The planner (``engine/planner.py``) turns mode + size + sampling (§2, §8)
into an exact/approximate/sampled/not_computed decision per row — no
``profile_<domain>()`` code path exists anywhere.

Phase-2/Final profilers (relationship, relational, drift) are intentionally
NOT registered: the §3 table marks them "Phase 2"/"Phase 4", and a metric
that cannot be planned must not exist in the registry (tests assert the
closed MVP set, mirroring the ADR-011 connector closed-set discipline).

SQL templates use ``{table}`` / ``{col}`` placeholders and cover the MVP
scalar aggregates only; multi-statement profilers (histogram binning,
frequency pages, gap scans) are built by their profiler tasks M2-T4..T6 and
register with ``sql_template=None`` until then — the registry is the plan
surface; execution lives in ``engine/metrics/profiler.py`` (M2-T4 runner
for the statistical + missingness families).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from data_profiling_engine.discovery.types import ColumnDiscovery, LogicalType

#: Rows above which the planner treats a dataset as "large" for cost gating.
#: Same 10K boundary discovery already uses for the exact↔HLL switch
#: (discovery/signals.py EXACT_DISTINCT_ROW_LIMIT, ENGINE §2.1 "large N").
LARGE_ROW_LIMIT = 10_000


class CostClass(str, Enum):
    """ENGINE §2.1 cost classes — planner gating behaviour is encoded in the
    member doc (the §2.1 "Default at large N" column verbatim)."""

    CHEAP_EXACT = "cheap_exact"            # exact always
    MODERATE_EXACT = "moderate_exact"      # exact under standard; sketch under quick
    EXPENSIVE_EXACT = "expensive_exact"    # approximate/sampled unless deep
    PROHIBITED_AT_SCALE = "prohibited_at_scale"  # never full; sample/cap with labeling


class MetricScope(str, Enum):
    """Where a metric is planned: once per dataset, or per applicable column."""

    DATASET = "dataset"
    COLUMN = "column"


@dataclass(frozen=True)
class MetricSpec:
    """One registry row. Field groups mirror ENGINE §3 ( Computes/MVP/Contract
    target) plus §4 selection inputs and §2.1 cost declaration."""

    metric_id: str                  # stable kebab-domain key, e.g. "stats.mean"
    family: str                     # §3 profiler name: schema|statistical|categorical|missingness|uniqueness|distribution|temporal|pattern
    display_name: str               # §3 "Computes"-style wording
    contract_target: Optional[str]  # dotted path from column_profile/dataset_profile; None = internal signal
    scope: MetricScope
    cost_class: CostClass
    #: ENGINE §4 gate 1 — applicable logical types; empty tuple = any column.
    applicable_logical: Tuple[LogicalType, ...] = ()
    #: Applicable physical families (signals._family buckets); empty = any.
    applicable_physical: Tuple[str, ...] = ()
    #: ENGINE §4 gate 2 — config `profiling.sections` entry that owns this
    #: metric; dataset-scope rows live in the always-on "schema" section.
    section: str = "schema"
    supports_approximate: bool = False
    #: Metric is only meaningful over the *full* relation (key-candidate
    #: scanning etc.); global sampling then degrades it to not_computed.
    requires_full_relation: bool = False
    #: Stable under sampling: cheap full scans stay `exact` even when a
    #: sampling window is configured (ENGINE §2.1 "exact always" honesty —
    #: we recompute exactly, we do not relabel a sample).
    exact_under_sampling: bool = False
    #: Fetch-time hazard (gotcha): temporal scalars need CAST(... AS VARCHAR)
    #: unless the source physical type is already a string.
    cast_varchar_on_fetch: bool = False
    sql_template: Optional[str] = None  # {table}/{col} single-scalar aggregate
    approx_sql_template: Optional[str] = None  # used when method == approximate
    mvp_active: bool = True             # closed set; False rows are rejected at load

    def applies_to(self, column: ColumnDiscovery) -> bool:
        """ENGINE §4 gates 1–4 evaluated against the discovery output (the
        planner's only column-level input)."""
        if self.applicable_logical and column.logical not in self.applicable_logical:
            return False
        if self.applicable_physical:
            from data_profiling_engine.discovery.signals import _family

            fam = _family(column.profile.physical_type)
            if fam not in self.applicable_physical:
                return False
        return True


# ---------------------------------------------------------------------------
# MVP-active catalog (the ENGINE §3 table rows marked "yes"/"partial")
# ---------------------------------------------------------------------------

_NUMERIC_LOGICAL = (
    LogicalType.INTEGER,
    LogicalType.FLOAT,
    LogicalType.DECIMAL,
    LogicalType.NUMERIC_CURRENCY,
)
_TEMPORAL_LOGICAL = (
    LogicalType.TEMPORAL_DATE,
    LogicalType.TEMPORAL_TIME,
    LogicalType.TEMPORAL_DATETIME,
)
_STRINGISH_LOGICAL = (
    LogicalType.STRING,
    LogicalType.TEXT_FREEFORM,
    LogicalType.CATEGORICAL_NOMINAL,
    LogicalType.EMAIL,
    LogicalType.URL,
    LogicalType.PHONE,
    LogicalType.UUID,
)

_SCHEMA_METRICS = (
    MetricSpec("schema.row_count", "schema", "row count",
               "dataset_profile.row_count", MetricScope.DATASET,
               CostClass.CHEAP_EXACT, exact_under_sampling=True,
               sql_template="SELECT count(*) FROM {table}"),
    MetricSpec("schema.column_count", "schema", "column count",
               "dataset_profile.column_count", MetricScope.DATASET,
               CostClass.CHEAP_EXACT, exact_under_sampling=True,
               sql_template="SELECT count(*) FROM pragma_table_info('{table}')"),
    MetricSpec("schema.physical_types", "schema", "physical types per column",
               "column_profile.physical_type", MetricScope.DATASET,
               CostClass.CHEAP_EXACT, exact_under_sampling=True),
    MetricSpec("schema.logical_semantic_types", "schema", "logical/semantic types",
               "column_profile.semantic_type", MetricScope.DATASET,
               CostClass.CHEAP_EXACT, exact_under_sampling=True),
    MetricSpec("schema.key_candidates", "schema", "key candidate columns",
               "dataset_profile.primary_key_candidates", MetricScope.DATASET,
               CostClass.EXPENSIVE_EXACT, supports_approximate=True,
               requires_full_relation=True),
)

_STATS_METRICS = (
    # Statistical Profiler → ColumnProfile.stats (ENGINE §3; numeric logicals
    # only per §4 gate 1: "statistical metrics only for numeric logical types")
    MetricSpec("stats.valid_count", "statistical", "non-null count",
               "column_profile.stats.valid_count", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="descriptive_statistics", exact_under_sampling=True,
               sql_template="SELECT count({col}) FROM {table}"),
    MetricSpec("stats.min", "statistical", "minimum",
               "column_profile.stats.min", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="descriptive_statistics", exact_under_sampling=True,
               sql_template="SELECT min({col}) FROM {table}"),
    MetricSpec("stats.max", "statistical", "maximum",
               "column_profile.stats.max", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="descriptive_statistics", exact_under_sampling=True,
               sql_template="SELECT max({col}) FROM {table}"),
    MetricSpec("stats.mean", "statistical", "arithmetic mean",
               "column_profile.stats.mean", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="descriptive_statistics", exact_under_sampling=True,
               sql_template="SELECT avg({col}) FROM {table}"),
    MetricSpec("stats.median", "statistical", "median (exact quantile)",
               "column_profile.stats.median", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="descriptive_statistics", supports_approximate=True,
               sql_template="SELECT quantile_cont({col}, 0.5) FROM {table}",
               approx_sql_template="SELECT quantile({col}, 0.5) FROM {table}"),
    MetricSpec("stats.p25", "statistical", "25th percentile",
               "column_profile.stats.p25", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="descriptive_statistics", supports_approximate=True,
               sql_template="SELECT quantile_cont({col}, 0.25) FROM {table}",
               approx_sql_template="SELECT quantile({col}, 0.25) FROM {table}"),
    MetricSpec("stats.p75", "statistical", "75th percentile",
               "column_profile.stats.p75", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="descriptive_statistics", supports_approximate=True,
               sql_template="SELECT quantile_cont({col}, 0.75) FROM {table}",
               approx_sql_template="SELECT quantile({col}, 0.75) FROM {table}"),
    MetricSpec("stats.p95", "statistical", "95th percentile",
               "column_profile.stats.p95", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="descriptive_statistics", supports_approximate=True,
               sql_template="SELECT quantile_cont({col}, 0.95) FROM {table}",
               approx_sql_template="SELECT quantile({col}, 0.95) FROM {table}"),
    MetricSpec("stats.p99", "statistical", "99th percentile",
               "column_profile.stats.p99", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="descriptive_statistics", supports_approximate=True,
               sql_template="SELECT quantile_cont({col}, 0.99) FROM {table}",
               approx_sql_template="SELECT quantile({col}, 0.99) FROM {table}"),
    MetricSpec("stats.stddev", "statistical", "sample standard deviation",
               "column_profile.stats.stddev", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="descriptive_statistics", supports_approximate=True,
               sql_template="SELECT stddev_samp({col}) FROM {table}"),
)

_MISSINGNESS_METRICS = (
    # Missingness Profiler → ColumnProfile.missingness. Counts are single-scan
    # aggregates = §2.1 "null count" cheap_exact tier; the sentinel VALUE SET
    # is config input — profiling.missing.sentinel_values, ADR-017 — so
    # detection itself stays cheap.
    # Sentinel templates carry a third placeholder, {sentinels}: a
    # parameterised relation ($1 = bound value list, ADR-017 — values are
    # never interpolated into SQL), substituted by planner.render_sql's
    # `params` kwarg before execution.
    MetricSpec("missingness.null_count", "missingness", "null count",
               "column_profile.missingness.null_count", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, section="missing_values",
               exact_under_sampling=True,
               sql_template="SELECT count(*) - count({col}) FROM {table}"),
    MetricSpec("missingness.blank_count", "missingness", "blank-string count",
               "column_profile.missingness.blank_count", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, applicable_physical=("varchar",),
               section="missing_values", exact_under_sampling=True,
               sql_template="SELECT count(CASE WHEN trim({col}) = '' THEN 1 END) FROM {table}"),
    MetricSpec("missingness.sentinel_count", "missingness",
               "sentinel-value count",
               "column_profile.missingness.sentinel_count", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, section="missing_values",
               exact_under_sampling=True,
               sql_template="SELECT count(*) FILTER (WHERE CAST({col} AS VARCHAR) "
                             "IN (SELECT value FROM {sentinels})) FROM {table}"),
    MetricSpec("missingness.missing_rate", "missingness",
               "missing rate + denominator (MetricValue)",
               "column_profile.missingness.missing_rate", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, section="missing_values",
               exact_under_sampling=True,
               # union predicate — a value both blank and sentinel counts once;
               # NULL never matches the sentinel set (ADR-017). Empty relation
               # → NULL → profiler normalises to 0.0 over denominator 0.
               sql_template="SELECT count(*) FILTER (WHERE {col} IS NULL OR "
                             "trim(CAST({col} AS VARCHAR)) = '' OR CAST({col} AS VARCHAR) "
                             "IN (SELECT value FROM {sentinels})) * 1.0 / count(*) FROM {table}"),
)

_UNIQUENESS_METRICS = (
    # Uniqueness Profiler → ColumnProfile.uniqueness. COUNT(DISTINCT) is
    # §2.1's named expensive_exact example on high cardinality; HLL is the
    # §2.2 approximation (algorithm recorded in method_params).
    MetricSpec("uniqueness.distinct_count", "uniqueness", "distinct count",
               "column_profile.uniqueness.distinct_count", MetricScope.COLUMN,
               CostClass.EXPENSIVE_EXACT, supports_approximate=True,
               section="uniqueness",
               sql_template="SELECT count(DISTINCT {col}) FROM {table}",
               approx_sql_template="SELECT approx_count_distinct({col}) FROM {table}"),
    MetricSpec("uniqueness.is_unique_candidate", "uniqueness",
               "uniqueness of the column (distinct == non-null count)",
               "column_profile.uniqueness.is_unique_candidate", MetricScope.COLUMN,
               CostClass.EXPENSIVE_EXACT, supports_approximate=True,
               section="uniqueness", requires_full_relation=True),
    MetricSpec("uniqueness.duplicate_groups", "uniqueness",
               "duplicate-key grouping count",
               None, MetricScope.COLUMN,
               CostClass.EXPENSIVE_EXACT, supports_approximate=True,
               section="uniqueness", requires_full_relation=True),
)

_CATEGORY_METRICS = (
    # Categorical Profiler → ColumnProfile.categorical. §2.1 puts "frequency
    # counts on low-cardinality" in moderate_exact; the low-cardinality gate
    # itself is the uniqueness.distinct_count result (§4 gate 1 input).
    MetricSpec("categorical.frequencies", "categorical",
               "category frequency counts",
               "column_profile.categorical.frequencies", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_STRINGISH_LOGICAL,
               section="categorical", supports_approximate=True),
    MetricSpec("categorical.rare_categories", "categorical", "rare categories",
               "column_profile.categorical.rare_categories", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_STRINGISH_LOGICAL,
               section="categorical", supports_approximate=True),
    MetricSpec("categorical.label_inconsistencies", "categorical",
               "label inconsistency (case/whitespace variants)",
               "column_profile.categorical.label_inconsistencies",
               MetricScope.COLUMN, CostClass.EXPENSIVE_EXACT,
               applicable_logical=_STRINGISH_LOGICAL, section="categorical",
               requires_full_relation=True),
)

_DISTRIBUTION_METRICS = (
    # Distribution Profiler → ColumnProfile.distribution. Histogram is one
    # width_bucket aggregation pass over an already-scanned numeric column:
    # bounded output (fixed bins, §2.2), so moderate_exact — cheap enough to
    # run in standard, sketched (sampled) under quick per §2.1.
    MetricSpec("distribution.histogram", "distribution",
               "fixed-bin histogram (width_bucket)",
               "column_profile.distribution.histogram", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="distributions", supports_approximate=True),
    MetricSpec("distribution.skewness", "distribution", "skewness",
               "column_profile.distribution.skewness", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="distributions", supports_approximate=True,
               sql_template="SELECT skewness({col}) FROM {table}"),
    MetricSpec("distribution.outlier_candidates", "distribution",
               "IQR/percentile outlier candidates",
               "column_profile.distribution.outlier_candidates", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_NUMERIC_LOGICAL,
               section="distributions", supports_approximate=True),
)

_TEMPORAL_METRICS = (
    # Temporal Profiler (partial, single-column only per §3) →
    # ColumnProfile.temporal. Range endpoints are cheap_exact min/max scalars
    # but MUST cast on fetch for TZ-bearing physicals (gotcha: no pytz).
    MetricSpec("temporal.range_min", "temporal", "range start",
               "column_profile.temporal.range.min", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, applicable_logical=_TEMPORAL_LOGICAL,
               section="temporal", exact_under_sampling=True,
               cast_varchar_on_fetch=True,
               sql_template="SELECT min({col}) FROM {table}"),
    MetricSpec("temporal.range_max", "temporal", "range end",
               "column_profile.temporal.range.max", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, applicable_logical=_TEMPORAL_LOGICAL,
               section="temporal", exact_under_sampling=True,
               cast_varchar_on_fetch=True,
               sql_template="SELECT max({col}) FROM {table}"),
    MetricSpec("temporal.volume_per_granularity", "temporal",
               "row volume bucketed by granularity",
               "column_profile.temporal.volume_per_granularity", MetricScope.COLUMN,
               CostClass.MODERATE_EXACT, applicable_logical=_TEMPORAL_LOGICAL,
               section="temporal", supports_approximate=True),
    MetricSpec("temporal.gaps", "temporal", "missing periods",
               "column_profile.temporal.gaps", MetricScope.COLUMN,
               CostClass.EXPENSIVE_EXACT, applicable_logical=_TEMPORAL_LOGICAL,
               section="temporal", requires_full_relation=True),
    MetricSpec("temporal.out_of_range", "temporal",
               "values outside declared expected range",
               "column_profile.temporal.out_of_expected_range", MetricScope.COLUMN,
               CostClass.CHEAP_EXACT, applicable_logical=_TEMPORAL_LOGICAL,
               section="temporal", exact_under_sampling=True),
)

_PATTERN_METRICS = (
    # Pattern Profiler (§3 "partial", feeds semantic + findings, no contract
    # block). Full-regex profiling of every VARCHAR row is §2.1's
    # "prohibited_at_scale" shape (O(n) heavy regex over wide/text columns):
    # the planner only ever allows it labeled `sampled` with a bounded size.
    MetricSpec("pattern.text_format", "pattern",
               "text/identifier format classification",
               None, MetricScope.COLUMN,
               CostClass.PROHIBITED_AT_SCALE, applicable_logical=_STRINGISH_LOGICAL,
               section="patterns", supports_approximate=True),
)

_MVP_CATALOG: Tuple[MetricSpec, ...] = (
    _SCHEMA_METRICS + _STATS_METRICS + _MISSINGNESS_METRICS +
    _UNIQUENESS_METRICS + _CATEGORY_METRICS + _DISTRIBUTION_METRICS +
    _TEMPORAL_METRICS + _PATTERN_METRICS
)


class MetricRegistryError(ValueError):
    """Registry construction failure (duplicate id, bad contract path,
    inactive row). Raised at load, never mid-run — an invalid catalog is a
    programming error, not a per-dataset data problem (AGENTS §5)."""


class MetricRegistry:
    """Immutable metric catalog. ``default_registry()`` is the process-wide
    MVP set; tests construct private registries to exercise edge behaviour
    without mutating the catalog."""

    def __init__(self, specs: List[MetricSpec]) -> None:
        by_id: Dict[str, MetricSpec] = {}
        for spec in specs:
            if not spec.mvp_active:
                raise MetricRegistryError(
                    f"{spec.metric_id}: non-MVP metrics must not be registered (ENGINE §3)"
                )
            if spec.metric_id in by_id:
                raise MetricRegistryError(f"duplicate metric_id {spec.metric_id}")
            by_id[spec.metric_id] = spec
        self._specs: Tuple[MetricSpec, ...] = tuple(specs)
        self._by_id = by_id

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def specs(self) -> Tuple[MetricSpec, ...]:
        return self._specs

    def get(self, metric_id: str) -> MetricSpec:
        try:
            return self._by_id[metric_id]
        except KeyError:
            raise MetricRegistryError(f"unknown metric_id {metric_id!r}") from None

    def by_family(self, family: str) -> Tuple[MetricSpec, ...]:
        return tuple(s for s in self._specs if s.family == family)

    def dataset_metrics(self) -> Tuple[MetricSpec, ...]:
        return tuple(s for s in self._specs if s.scope is MetricScope.DATASET)

    def metrics_for_column(self, column: ColumnDiscovery) -> Tuple[MetricSpec, ...]:
        """ENGINE §4 selection for one discovered column: type applicability
        (gates 1) in catalog order. Section/mode gating is the planner's job."""
        return tuple(
            s for s in self._specs
            if s.scope is MetricScope.COLUMN and s.applies_to(column)
        )


_DEFAULT: Optional[MetricRegistry] = None


def default_registry() -> MetricRegistry:
    """Process-wide MVP registry, built once from :data:`_MVP_CATALOG`."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = MetricRegistry(list(_MVP_CATALOG))
    return _DEFAULT

