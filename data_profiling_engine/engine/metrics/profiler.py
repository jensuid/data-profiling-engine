"""Metric-family profiler runners (M2-T4 statistical + missingness;
M2-T5 uniqueness + categorical; M2-T6 distribution + temporal).
ENGINE §3; TESTING §3.1.

Executes the M2-T3 plan: consumes an :class:`ExecutionPlan`, renders each
templated decision through :func:`render_sql` (the single SQL materialisation
point) against the bound DuckDB relation, and stamps the scalars into the
matching ``ColumnProfile`` sub-blocks as contract :class:`MetricValue` /
boolean / array objects carrying
``Provenance(**decision.provenance_kwargs())`` verbatim (ADR-015 — sampled
needs seed, approximate needs algorithm, not_computed needs reason and NO
value). Metrics the M2-T3 registry declares without a template (uniqueness
``is_unique_candidate``/``duplicate_groups`` and every categorical row) build
their SQL here — the registry is the *plan* surface; multi-statement /
multi-column aggregates are the *execution* surface.

Obligations honoured here:

- **Never a full materialisation** (ADR-007): every metric is a one-row
  scalar aggregate over the DuckDB view; the only extra relation ever built
  is the bounded sample (≤ ``sampling.size`` rows) and a 1-column sentinel
  relation.
- **Sampling honesty** (ENGINE §2.2): a ``sampled`` decision runs the same
  SQL against one shared bounded sample relation; the label + strategy +
  size + seed live in the Provenance, never in a different query. The
  bounded relation is a real seeded reservoir (M2-T10): ``reservoir`` draws
  ``size`` rows via DuckDB ``USING SAMPLE RESERVOIR (n ROWS) REPEATABLE
  (seed)`` — uniform over the whole relation and reproducible for a seed;
  ``block`` honours locality via an unordered bounded scan (ENGINE §2.2).
- **Partial-failure policy** (CONTRACTS §6.4): a metric task that raises is
  recorded as ``not_computed`` + reason and a :class:`RunErrorEntry`
  (§8 ``METRIC_NOT_COMPUTED``); other tasks keep executing.
- **Sentinel values are config input, bound not interpolated** (ADR-017):
  the set is inserted via ``executemany($1)`` into a one-column temp
  relation read by the templated SQL — user data never enters the SQL string.
- **not_computed decisions are structurally skipped** (planner §2.2): the
  MetricValue lands as ``not_computed`` + reason with the ``value`` key
  ABSENT; the plain-int contract slots (``valid_count``, ``null_count``,
  ``blank_count``, ``sentinel_count``) have no provenance carrier, so a
  skipped count reads 0 and the reason stays on the planner decision
  (``plan.skipped()``) for the run record — never a silent null.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import duckdb

from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery.types import ColumnDiscovery, DiscoveryReport
from data_profiling_engine.engine.metrics import MetricRegistry, default_registry
from data_profiling_engine.engine.planner import (
    ExecutionPlan,
    PlanDecision,
    render_sql,
    _q as _quote_ident,
)
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.schemas.models import (
    Categorical,
    ColumnProfile,
    DatasetConfig,
    Distribution,
    Histogram,
    MetricMethod,
    MetricValue,
    Missingness,
    NumericStats,
    OutlierCandidates,
    Provenance,
    RunErrorEntry,
    Temporal,
    TemporalRange,
    Uniqueness,
)

#: Families executed by the M2-T4 runner; uniqueness/categorical (M2-T5, the
#: :func:`execute_uniqueness_and_categorical` pass below) and
#: distribution/temporal (M2-T6) decisions in the same plan are left alone.
EXECUTED_FAMILIES: Tuple[str, ...] = ("statistical", "missingness")

#: Families executed by the M2-T5 runner.
UNIQUENESS_CATEGORICAL_FAMILIES: Tuple[str, ...] = ("uniqueness", "categorical")

#: Families executed by the M2-T6 runner (distribution + single-column temporal).
DISTRIBUTION_TEMPORAL_FAMILIES: Tuple[str, ...] = ("distribution", "temporal")

#: metric_id → NumericStats field (catalog order preserved).
_STATS_TARGETS: Dict[str, str] = {
    "stats.valid_count": "valid_count",
    "stats.min": "min",
    "stats.max": "max",
    "stats.mean": "mean",
    "stats.median": "median",
    "stats.p25": "p25",
    "stats.p75": "p75",
    "stats.p95": "p95",
    "stats.p99": "p99",
    "stats.stddev": "stddev",
}

#: Plain-int slots on Missingness (contract has no MetricValue there — only
#: missing_rate carries provenance; method honesty rides on the rate).
_MISSINGNESS_INT_TARGETS: Dict[str, str] = {
    "missingness.null_count": "null_count",
    "missingness.blank_count": "blank_count",
    "missingness.sentinel_count": "sentinel_count",
}

#: DuckDB errors on a scalar probe can echo an offending value; SECURITY §5 —
#: reasons/error messages stay bounded and never carry raw dataset content.
_MAX_REASON_LEN = 300

#: Bound sentinel relation for the {sentinels} placeholder (ADR-017): a temp
#: 1-column table filled via executemany so values are never interpolated.
_SENTINEL_TABLE = "dpe_sentinels"


@dataclass(frozen=True)
class ProfilerResult:
    """Outcome of one M2-T4 or M2-T5 execution pass."""

    profiles: Tuple[ColumnProfile, ...]
    errors: Tuple[RunErrorEntry, ...] = ()
    sample_view: Optional[str] = None  # bounded relation name, None if no sampling
    #: Uniqueness.auxiliary: column → duplicate-key grouping count. The
    #: metric's contract_target is None (M2-T3 registry) — it exists as an
    #: internal signal for M2-T8 findings, never lands on ColumnProfile.
    duplicate_group_counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class _ColumnSlot:
    """Mutable assembly bucket per column: computed stats values, the
    missingness counts, and the missing_rate MetricValue."""

    stats: Dict[str, Any] = field(default_factory=dict)   # stats field -> int | MetricValue
    counts: Dict[str, int] = field(default_factory=dict)  # missingness int field -> int
    rate: Optional[MetricValue] = None


def _normalize(value: Any) -> Any:
    """DuckDB scalar → JSON-safe contract value. DECIMAL fetches arrive as
    :class:`Decimal` (not JSON-serialisable); MetricValue.value is a number,
    so we narrow to float — ``exact`` provenance is retained because the
    *computation* was exact, only the transport type widens."""
    if isinstance(value, Decimal):
        return float(value)
    return value


def _reason(exc: Exception) -> str:
    """Bounded, non-echoing error text for not_computed reasons."""
    text = " ".join(str(exc).split())
    return text[:_MAX_REASON_LEN]


def _not_computed_value(reason: str, denominator: Optional[int] = None) -> MetricValue:
    """MetricValue at not_computed must OMIT the ``value`` key entirely (schema
    allOf + model validator). ``model_construct`` skips validators; dropping
    ``value`` from ``model_fields_set`` makes the canonical dump omit it."""
    kwargs: Dict[str, Any] = {
        "value": None,
        "provenance": Provenance(method=MetricMethod.NOT_COMPUTED, reason=reason),
    }
    if denominator is not None:
        kwargs["denominator"] = denominator
    mv = MetricValue.model_construct(**kwargs)
    mv.model_fields_set.discard("value")
    return mv


def _metric_value(value: Any, decision: PlanDecision,
                  denominator: Optional[int] = None) -> MetricValue:
    """Build a contract MetricValue; ``denominator`` is OMITTED when absent
    (strict-schema models reject an explicit null on a non-nullable field —
    same rule as ``Provenance``, gotcha M1-T4)."""
    if denominator is None:
        return MetricValue(
            value=_normalize(value),
            provenance=Provenance(**decision.provenance_kwargs()),
        )
    return MetricValue(
        value=_normalize(value),
        provenance=Provenance(**decision.provenance_kwargs()),
        denominator=denominator,
    )


def _scalar(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: Sequence[Sequence] = (),
) -> Any:
    cur = con.execute(sql, list(params)) if params else con.execute(sql)
    row = cur.fetchone()
    return row[0] if row else None


def _relation_row_count(con: duckdb.DuckDBPyConnection, view: str) -> int:
    return int(con.execute(f'SELECT count(*) FROM "{view}"').fetchone()[0])


def _sample_relation(
    con: duckdb.DuckDBPyConnection,
    base_view: str,
    strategy: str,
    size: int,
    seed: int,
) -> str:
    """Build the one bounded sample relation shared by every ``sampled``
    decision (≤ ``size`` rows — ADR-007). Finalised in M2-T10 as a *real*
    reservoir, no longer the preliminary md5 ORDER+LIMIT hash:

    - ``reservoir`` (default) draws ``size`` rows uniformly from the whole
      relation with DuckDB ``USING SAMPLE RESERVOIR (size ROWS) REPEATABLE
      (seed)`` — reproducible for a given seed (different seed ⇒ different
      subset), and returns all rows when the relation is smaller than
      ``size`` (ENGINE §2.2 "representative subsets").
    - ``block`` honours locality via an unordered bounded scan
      (``LIMIT size``) — only used when a metric genuinely needs it.

    Deterministic per (view, strategy, size, seed); the name is content-hashed
    so identical windows reuse one relation and never clobber a user object.
    ``REPEATABLE`` is bound as an integer literal (a planner-verified seed,
    never user data), so no raw value ever enters the SQL string."""
    tag = hashlib.sha256(
        f"{base_view}|{strategy}|{size}|{seed}".encode("utf-8")
    ).hexdigest()[:12]
    name = f"dpe_sample_{tag}"
    if strategy == "block":
        body = f'SELECT * FROM "{base_view}" LIMIT {int(size)}'
    else:
        body = (
            f'SELECT * FROM "{base_view}" '
            f"USING SAMPLE RESERVOIR ({int(size)} ROWS) REPEATABLE ({int(seed)})"
        )
    con.execute(f'DROP TABLE IF EXISTS "{name}"')
    con.execute(f'CREATE OR REPLACE TEMP TABLE "{name}" AS {body}')
    return name


def _prepare_sentinels(con: duckdb.DuckDBPyConnection, values: Sequence[str]) -> None:
    """ADR-017 binding seam: sentinel placeholders are data, never SQL —
    loaded via ``$1`` parameters into a temp relation the templates read."""
    con.execute(f'CREATE OR REPLACE TEMP TABLE "{_SENTINEL_TABLE}" (value VARCHAR)')
    if values:
        con.executemany(
            f'INSERT INTO "{_SENTINEL_TABLE}" VALUES ($1)', [[v] for v in values]
        )


def _targets_column(plan: ExecutionPlan, metric_ids: Sequence[str]) -> bool:
    """Did the planner schedule at least one of these metrics for any column
    (absent family ⇒ block stays absent, not a zeroed fabrication)."""
    wanted = set(metric_ids)
    return any(
        d.metric_id in wanted
        for cp in plan.column_plans
        for d in cp.decisions
    )


def execute_stat_and_missingness(
    con: duckdb.DuckDBPyConnection,
    handle: RelationHandle,
    report: DiscoveryReport,
    plan: ExecutionPlan,
    config: Optional[DatasetConfig] = None,
    registry: Optional[MetricRegistry] = None,
) -> ProfilerResult:
    """Run every statistical + missingness decision in ``plan`` against
    ``handle``'s relation; return updated ColumnProfiles (identity-preserving
    per column order) plus per-task §6.4 error entries.

    ``config`` supplies the sentinel set (ADR-017) and the sampling window;
    ``not_computed`` decisions are skipped structurally (values ABSENT with
    reason, never silent nulls — AGENTS §3)."""
    reg = registry if registry is not None else default_registry()
    errors: List[RunErrorEntry] = []

    decisions = [
        (cp.column.profile.column, d)
        for cp in plan.column_plans
        for d in cp.decisions
    ]

    # --- sampling window (one shared bounded relation) ----------------------
    sampling = config.profiling.sampling if config else None
    sample_view: Optional[str] = None
    if any(d.method is MetricMethod.SAMPLED for _, d in decisions):
        if not (sampling and sampling.enabled and sampling.size and sampling.seed is not None):
            raise ValueError(
                "plan carries sampled decisions but no sampling window is configured"
            )
        sample_view = _sample_relation(
            con, handle.view_name, sampling.strategy or "reservoir",
            sampling.size, sampling.seed,
        )

    # --- sentinel set (bound, never interpolated — ADR-017) -----------------
    sentinel_values: List[str] = []
    if config and config.profiling.missing and config.profiling.missing.sentinel_values:
        sentinel_values = list(config.profiling.missing.sentinel_values)
    needs_sentinels = any(
        "{sentinels}" in (reg.get(d.metric_id).sql_template or "")
        for _, d in decisions
        if d.method is not MetricMethod.NOT_COMPUTED
    )
    if needs_sentinels:
        _prepare_sentinels(con, sentinel_values)

    physical_of: Dict[str, str] = dict(handle.columns)
    slots: Dict[str, _ColumnSlot] = {}

    for column, decision in decisions:
        spec = reg.get(decision.metric_id)
        if spec.family not in EXECUTED_FAMILIES:
            continue  # M2-T5/T6 families ride in later tasks
        slot = slots.setdefault(column, _ColumnSlot())
        phys = physical_of.get(column, "")

        if decision.method is MetricMethod.NOT_COMPUTED:
            # Structurally skipped (planner §2.2): absent value + reason.
            # MetricValue slots carry the honest not_computed stamp; plain-int
            # slots have no provenance carrier (CONTRACTS §3.2), they read 0
            # and the decision stays visible via plan.skipped().
            target = _STATS_TARGETS.get(decision.metric_id)
            if decision.metric_id == "missingness.missing_rate":
                slot.rate = _not_computed_value(decision.reason or "not computed")
            elif target is not None and target != "valid_count":
                slot.stats[target] = _not_computed_value(decision.reason or "not computed")
            continue

        view = sample_view if decision.method is MetricMethod.SAMPLED else handle.view_name
        extra: Dict[str, str] = {}
        if "{sentinels}" in (spec.sql_template or ""):
            extra["sentinels"] = f'"{_SENTINEL_TABLE}"'
        # A `approximate` decision whose spec carries no approx template (e.g.
        # stddev under quick — DuckDB has no cheaper sketch) still executes the
        # exact SQL; the M2-T3 planner delegated this "execution detail" to the
        # profiler, which keeps the planner's `approximate`+algorithm label
        # verbatim (over-cautious: an exact value is never relabelled
        # `exact`-forbidden per ADR-015 — we under-claim, never over-claim).
        sql_method = decision.method
        if decision.method is MetricMethod.APPROXIMATE and not spec.approx_sql_template:
            sql_method = MetricMethod.EXACT

        try:
            value: Any
            if decision.metric_id == "missingness.missing_rate":
                # denominator = row count of the SAME scanned relation (ENGINE §3
                # note): sample window when sampled, full view otherwise. An
                # empty relation ⇒ rate 0.0 (no rows can be missing); the SQL
                # divides by count(*), so we skip it rather than hit 0/0.
                denominator = _relation_row_count(con, view)
                if denominator == 0:
                    slot.rate = _metric_value(0.0, decision, denominator=0)
                else:
                    value = _scalar(con, render_sql(
                        spec, view, column=column, physical_type=phys,
                        method=sql_method, params=extra))
                    rate = 0.0 if value is None else float(value)
                    slot.rate = _metric_value(rate, decision, denominator=denominator)
            else:
                value = _scalar(con, render_sql(
                    spec, view, column=column, physical_type=phys,
                    method=sql_method, params=extra))
                target = _STATS_TARGETS.get(decision.metric_id)
                if target is not None:
                    if target == "valid_count":
                        slot.stats[target] = int(value or 0)
                    else:
                        slot.stats[target] = _metric_value(value, decision)
                elif decision.metric_id in _MISSINGNESS_INT_TARGETS:
                    slot.counts[_MISSINGNESS_INT_TARGETS[decision.metric_id]] = int(value or 0)
        except duckdb.Error as exc:
            # §6.4 partial failure: record + mark not_computed, keep running.
            reason = f"metric execution failed: {_reason(exc)}"
            errors.append(RunErrorEntry(
                code=ErrorCode.METRIC_NOT_COMPUTED.value,
                task=f"{decision.metric_id}:{column}",
                message=reason,
            ))
            if decision.metric_id == "missingness.missing_rate":
                slot.rate = _not_computed_value(reason)
            else:
                target = _STATS_TARGETS.get(decision.metric_id)
                if target is not None and target != "valid_count":
                    # MetricValue slot → honest absence with reason
                    slot.stats[target] = _not_computed_value(reason)
                # plain-int slots stay 0; the reason lives on the error entry —
                # the contract shape has no provenance carrier there (CONTRACTS §3.2)

    # --- assemble blocks -----------------------------------------------------
    missingness_planned = _targets_column(
        plan, list(_MISSINGNESS_INT_TARGETS) + ["missingness.missing_rate"])
    profiles: List[ColumnProfile] = []
    for cd in report.columns:
        profile = cd.profile
        slot = slots.get(profile.column, _ColumnSlot())
        update: Dict[str, Any] = {}
        if slot.stats:
            update["stats"] = NumericStats(**slot.stats)
        if missingness_planned:
            update["missingness"] = Missingness(
                null_count=slot.counts.get("null_count", 0),
                blank_count=slot.counts.get("blank_count", 0),
                sentinel_count=slot.counts.get("sentinel_count", 0),
                missing_rate=slot.rate
                if slot.rate is not None
                else _not_computed_value("missing_rate was not planned for this column"),
            )
        profiles.append(profile.model_copy(update=update) if update else profile)

    return ProfilerResult(tuple(profiles), tuple(errors), sample_view)


# ===========================================================================
# M2-T5 — Uniqueness + Categorical runner
# ===========================================================================

#: ENGINE §3 "Categorical Profiler": frequency / cardinality / rare categories
#: / label inconsistency. Enumeration is only useful for a bounded category
#: set — the M2-T3 registry leaves the low-cardinality gate to the profiler
#: (STATE M2-T5 NEXT pointer), so this runner reads the just-computed
#: ``uniqueness.distinct_count`` and refuses to emit a categorical block when
#: the estimate exceeds ``CATEGORICAL_MAX_DISTINCT``. Values are heuristics
#: chosen for MVP usefulness + bounded payload (bridge §5.4 1 MiB cap),
#: recorded here — no per-domain code path exists (AGENTS §5).
CATEGORICAL_TOP_K = 50
CATEGORICAL_MAX_DISTINCT = 1_000

#: A category is *rare* when its frequency is ``<= max(1, floor(share * n))``
#: where ``n`` is the non-null row count of the scanned relation. 0.1 % is the
#: MVP heuristic (long-tail typo / one-off detection); the floor keeps the
#: rule meaningful on tiny fixtures where 0.1 % would round to 0.
RARE_FREQUENCY_SHARE = 0.001

#: Cap for label-inconsistency groups emitted per column (bounded payload).
LABEL_INCONSISTENCY_MAX_GROUPS = 100


@dataclass
class _UniCatSlot:
    """Mutable per-column assembly bucket for the M2-T5 pass."""

    distinct_count: Optional[MetricValue] = None
    is_unique_planned: bool = False
    is_unique_candidate: Optional[bool] = None
    non_null_count: Optional[int] = None
    #: set True whenever the planner scheduled at least one uniqueness
    #: decision that was NOT structurally skipped (§4 gate 2 section off ⇒
    #: every uniqueness decision becomes not_computed and the whole
    #: ColumnProfile.uniqueness block stays absent — never fabricated).
    uniqueness_executed: bool = False
    # categorical emission buffers
    categories: Optional[List[str]] = None
    frequencies: Optional[List[int]] = None
    rare_categories: Optional[List[str]] = None
    label_inconsistencies: Optional[List[Dict[str, Any]]] = None
    freq_computed: bool = False
    categorical_planned: bool = False


def _fetch_rows(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: Sequence[Sequence] = (),
) -> List[Tuple[Any, ...]]:
    cur = con.execute(sql, list(params)) if params else con.execute(sql)
    return cur.fetchall()


def _build_non_null_count_sql(view: str, column: str) -> str:
    return f"SELECT count({_quote_ident(column)}) FROM {_quote_ident(view)}"


def _build_distinct_count_exact_sql(view: str, column: str) -> str:
    return f"SELECT count(DISTINCT {_quote_ident(column)}) FROM {_quote_ident(view)}"


def _build_duplicate_group_sql(view: str, column: str) -> str:
    """Number of DISTINCT non-null values that occur more than once. Only
    groups (not affected-row counts) — ENGINE §3 "duplicate keys". NULLs are
    excluded (a NULL is not a duplicate key by definition; CONTRACTS §3.2
    missingness owns null handling)."""
    col = _quote_ident(column)
    return (
        f"SELECT count(*) FROM ("
        f"SELECT {col} FROM {_quote_ident(view)} "
        f"WHERE {col} IS NOT NULL GROUP BY {col} HAVING count(*) > 1"
        f")"
    )


def _build_frequencies_sql(view: str, column: str, top_k: int) -> str:
    """Top-K categories by frequency, ``cat ASC`` tie-break for determinism
    (§2.1: bounded output ⇒ we never ship an unbounded GROUP BY). NULLs are
    excluded — Missingness Profiler owns them (ENGINE §3 separation)."""
    col = _quote_ident(column)
    return (
        f"SELECT CAST({col} AS VARCHAR), count(*) "
        f"FROM {_quote_ident(view)} WHERE {col} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 2 DESC, 1 ASC LIMIT {int(top_k)}"
    )


def _build_label_variants_sql(view: str, column: str) -> str:
    """Fetch every (normalized, raw, count) triple so the profiler can
    group case/whitespace variants. Normalisation = ``lower(trim(...))`` —
    ENGINE §3 "label inconsistency" MVP interpretation; richer Unicode
    folding is Phase-4. Bounded by ``LABEL_INCONSISTENCY_MAX_GROUPS``
    *after* grouping (rows are pre-sorted so the fetch stays cheap)."""
    col = _quote_ident(column)
    return (
        f"SELECT lower(trim(CAST({col} AS VARCHAR))), CAST({col} AS VARCHAR), count(*) "
        f"FROM {_quote_ident(view)} WHERE {col} IS NOT NULL "
        f"GROUP BY 1, 2 ORDER BY 1 ASC, 2 ASC"
    )


def _collect_label_inconsistencies(
    rows: Iterable[Tuple[str, str, int]]
) -> List[Dict[str, Any]]:
    """Keep only normalized keys with two-or-more distinct raw variants.
    Deterministic: input is pre-sorted ``norm ASC, raw ASC``; groups inherit
    that order; we stop at ``LABEL_INCONSISTENCY_MAX_GROUPS``."""
    by_norm: Dict[str, List[Tuple[str, int]]] = {}
    for norm, raw, cnt in rows:
        by_norm.setdefault(norm, []).append((raw, int(cnt)))
    out: List[Dict[str, Any]] = []
    for norm in sorted(by_norm):
        variants = by_norm[norm]
        if len(variants) < 2:
            continue
        out.append({
            "normalized": norm,
            "variants": [v for v, _ in variants],
            "counts": [c for _, c in variants],
        })
        if len(out) >= LABEL_INCONSISTENCY_MAX_GROUPS:
            break
    return out


def execute_uniqueness_and_categorical(
    con: duckdb.DuckDBPyConnection,
    handle: RelationHandle,
    report: DiscoveryReport,
    plan: ExecutionPlan,
    config: Optional[DatasetConfig] = None,
    registry: Optional[MetricRegistry] = None,
) -> ProfilerResult:
    """Run every uniqueness + categorical decision in ``plan`` against
    ``handle``'s relation and return updated ColumnProfiles.

    Family scope: :data:`UNIQUENESS_CATEGORICAL_FAMILIES`. Other families in
    the same plan are ignored — each M2-T4..T6 runner owns its slice.

    Contract surface (ENGINE §3):
      - ``ColumnProfile.uniqueness`` — :class:`Uniqueness`(distinct_count
        MetricValue, is_unique_candidate bool). Emitted only when at least
        one uniqueness decision was planned AND ``distinct_count`` landed
        (either a value or an honest not_computed).
      - ``ColumnProfile.categorical`` — :class:`Categorical`(categories,
        frequencies, rare_categories, label_inconsistencies). Emitted only
        when the just-computed ``uniqueness.distinct_count`` value is
        ``<= CATEGORICAL_MAX_DISTINCT`` (the §4 low-cardinality gate STATE
        describes) — otherwise the whole block stays absent (never a
        truncated lie about the true category set).
      - ``uniqueness.duplicate_groups`` has ``contract_target=None`` — the
        count rides on :attr:`ProfilerResult.duplicate_group_counts` for
        M2-T8 findings, never on the persisted profile.

    Honesty rules honoured (ADR-015 / §6.4 / AGENTS §3):
      - ``not_computed`` ⇒ ``value`` key ABSENT on MetricValue + reason.
      - ``approximate`` for a metric with an approx template (distinct_count
        → HLL) executes the sketch SQL; the label + ``algorithm`` ride in
        Provenance verbatim.
      - ``approximate`` for a metric without an approx template (is_unique_candidate,
        frequencies, rare_categories, label_inconsistencies) executes the
        EXACT built SQL but keeps the planner's approximate label — the same
        delegation M2-T4 established for stddev/quick (see module docstring
        and gotcha entry).
      - ``sampled`` decisions run the identical built SQL against the one
        shared bounded sample relation seeded by ``sampling.strategy/size/seed``.
      - ``duckdb.Error`` on any one task ⇒ :class:`RunErrorEntry`
        (``METRIC_NOT_COMPUTED``) + a not_computed MetricValue where the
        slot exists; siblings keep running (§6.4).
    """
    reg = registry if registry is not None else default_registry()
    errors: List[RunErrorEntry] = []

    uniqcat: List[Tuple[str, ColumnDiscovery, PlanDecision]] = []
    for cp in plan.column_plans:
        for d in cp.decisions:
            spec = reg.get(d.metric_id)
            if spec.family in UNIQUENESS_CATEGORICAL_FAMILIES:
                uniqcat.append((cp.column.profile.column, cp.column, d))

    # --- sampling window (one shared bounded relation, same policy as M2-T4) --
    sampling = config.profiling.sampling if config else None
    sample_view: Optional[str] = None
    if any(d.method is MetricMethod.SAMPLED for _, _, d in uniqcat):
        if not (sampling and sampling.enabled and sampling.size and sampling.seed is not None):
            raise ValueError(
                "plan carries sampled uniqueness/categorical decisions but "
                "no sampling window is configured"
            )
        sample_view = _sample_relation(
            con, handle.view_name, sampling.strategy or "reservoir",
            sampling.size, sampling.seed,
        )

    physical_of: Dict[str, str] = dict(handle.columns)
    slots: Dict[str, _UniCatSlot] = {}
    duplicate_group_counts: Dict[str, int] = {}

    def _slot(column: str) -> _UniCatSlot:
        return slots.setdefault(column, _UniCatSlot())

    def _view_for(decision: PlanDecision) -> str:
        return sample_view if decision.method is MetricMethod.SAMPLED else handle.view_name

    # ---------------------------------------------------------------------
    # Pass 1: uniqueness
    # ---------------------------------------------------------------------
    for column, _cd, decision in uniqcat:
        spec = reg.get(decision.metric_id)
        if spec.family != "uniqueness":
            continue
        slot = _slot(column)
        phys = physical_of.get(column, "")
        view = _view_for(decision)

        if decision.method is MetricMethod.NOT_COMPUTED:
            if decision.metric_id == "uniqueness.distinct_count":
                slot.distinct_count = _not_computed_value(decision.reason or "not computed")
            elif decision.metric_id == "uniqueness.is_unique_candidate":
                slot.is_unique_planned = True
            # duplicate_groups contract_target=None → nothing to record
            continue

        slot.uniqueness_executed = True
        try:
            if decision.metric_id == "uniqueness.distinct_count":
                sql_method = decision.method
                if decision.method is MetricMethod.APPROXIMATE and not spec.approx_sql_template:
                    sql_method = MetricMethod.EXACT
                sql = render_sql(
                    spec, view, column=column, physical_type=phys, method=sql_method,
                )
                value = _scalar(con, sql)
                slot.distinct_count = _metric_value(int(value or 0), decision)
            elif decision.metric_id == "uniqueness.is_unique_candidate":
                slot.is_unique_planned = True
                # value derived in Pass 2 (needs distinct_count + non_null count).
            elif decision.metric_id == "uniqueness.duplicate_groups":
                value = _scalar(con, _build_duplicate_group_sql(view, column))
                duplicate_group_counts[column] = int(value or 0)
        except duckdb.Error as exc:
            reason = f"metric execution failed: {_reason(exc)}"
            errors.append(RunErrorEntry(
                code=ErrorCode.METRIC_NOT_COMPUTED.value,
                task=f"{decision.metric_id}:{column}",
                message=reason,
            ))
            if decision.metric_id == "uniqueness.distinct_count":
                slot.distinct_count = _not_computed_value(reason)

    # ---------------------------------------------------------------------
    # Pass 2: derive is_unique_candidate (distinct == non-null). Both sides
    # are counted against the SAME scanned relation the planner targeted.
    # ---------------------------------------------------------------------
    for column, _cd, decision in uniqcat:
        spec = reg.get(decision.metric_id)
        if spec.family != "uniqueness" or decision.metric_id != "uniqueness.is_unique_candidate":
            continue
        if decision.method is MetricMethod.NOT_COMPUTED:
            continue
        slot = _slot(column)
        if slot.distinct_count is None:
            # distinct_count was never planned or errored before landing —
            # no basis to derive the boolean; block stays absent (assembled
            # below only when uniqueness_planned AND distinct_count is set).
            continue
        try:
            view = _view_for(decision)
            non_null = int(_scalar(con, _build_non_null_count_sql(view, column)) or 0)
            slot.non_null_count = non_null
            distinct_val = slot.distinct_count.value
            if distinct_val is None:
                # honest absence propagates: no value ⇒ no candidate claim
                slot.is_unique_candidate = False
            else:
                slot.is_unique_candidate = (int(distinct_val) == non_null)
        except duckdb.Error as exc:
            reason = f"metric execution failed: {_reason(exc)}"
            errors.append(RunErrorEntry(
                code=ErrorCode.METRIC_NOT_COMPUTED.value,
                task=f"{decision.metric_id}:{column}",
                message=reason,
            ))
            slot.is_unique_candidate = False

    # ---------------------------------------------------------------------
    # Pass 3: categorical — gated on uniqueness.distinct_count ≤ threshold.
    # Arrays have no provenance carrier on the contract surface, so the
    # honesty stamp rides on the block's *presence*: high-cardinality /
    # not_computed distinct_count ⇒ whole categorical block omitted (never
    # a truncated top-K masquerading as the full enumeration).
    # ---------------------------------------------------------------------
    def _low_cardinality_ok(slot: _UniCatSlot) -> bool:
        if slot.distinct_count is None:
            return False
        dv = slot.distinct_count.value
        if dv is None:
            return False
        try:
            return int(dv) <= CATEGORICAL_MAX_DISTINCT
        except (TypeError, ValueError):
            return False

    for column, _cd, decision in uniqcat:
        spec = reg.get(decision.metric_id)
        if spec.family != "categorical":
            continue
        slot = _slot(column)
        slot.categorical_planned = True
        if decision.method is MetricMethod.NOT_COMPUTED:
            continue
        if not _low_cardinality_ok(slot):
            continue
        view = _view_for(decision)
        try:
            if decision.metric_id in ("categorical.frequencies", "categorical.rare_categories"):
                if not slot.freq_computed:
                    rows = _fetch_rows(con, _build_frequencies_sql(view, column, CATEGORICAL_TOP_K))
                    slot.categories = [str(r[0]) for r in rows]
                    slot.frequencies = [int(r[1]) for r in rows]
                    slot.freq_computed = True
                if decision.metric_id == "categorical.rare_categories":
                    if slot.non_null_count is None:
                        slot.non_null_count = int(
                            _scalar(con, _build_non_null_count_sql(view, column)) or 0
                        )
                    threshold = max(1, math.floor(slot.non_null_count * RARE_FREQUENCY_SHARE))
                    slot.rare_categories = [
                        c for c, f in zip(slot.categories or [], slot.frequencies or [])
                        if f <= threshold
                    ]
            elif decision.metric_id == "categorical.label_inconsistencies":
                rows = _fetch_rows(con, _build_label_variants_sql(view, column))
                slot.label_inconsistencies = _collect_label_inconsistencies(rows)
        except duckdb.Error as exc:
            reason = f"metric execution failed: {_reason(exc)}"
            errors.append(RunErrorEntry(
                code=ErrorCode.METRIC_NOT_COMPUTED.value,
                task=f"{decision.metric_id}:{column}",
                message=reason,
            ))

    # ---------------------------------------------------------------------
    # Assemble blocks
    # ---------------------------------------------------------------------
    profiles: List[ColumnProfile] = []
    for cd in report.columns:
        profile = cd.profile
        slot = slots.get(profile.column, _UniCatSlot())
        update: Dict[str, Any] = {}
        if slot.uniqueness_executed and slot.distinct_count is not None:
            update["uniqueness"] = Uniqueness(
                distinct_count=slot.distinct_count,
                is_unique_candidate=bool(slot.is_unique_candidate),
            )
        if slot.categorical_planned and slot.freq_computed:
            # Pydantic strict models reject an explicit null on a non-nullable
            # field (M1-T4 gotcha); only pass keys we actually populated.
            cat_kwargs: Dict[str, Any] = {
                "categories": slot.categories,
                "frequencies": slot.frequencies,
            }
            if slot.rare_categories is not None:
                cat_kwargs["rare_categories"] = slot.rare_categories
            if slot.label_inconsistencies is not None:
                cat_kwargs["label_inconsistencies"] = slot.label_inconsistencies
            update["categorical"] = Categorical(**cat_kwargs)
        profiles.append(profile.model_copy(update=update) if update else profile)

    return ProfilerResult(
        tuple(profiles), tuple(errors), sample_view, duplicate_group_counts,
    )


# ===========================================================================
# M2-T6 — Distribution + single-column Temporal runner
# ===========================================================================

#: ENGINE §2.2: "Histograms: fixed bin count with width_bucket; bin strategy
#: recorded in method_params". DuckDB 2.0 exposes **no** ``width_bucket``
#: scalar (STATE gotcha), so the bin index is emulated with the identical
#: arithmetic (``LEAST(nb, floor((x-lo)/(hi-lo)*nb)+1)``) in the runner. A
#: fixed count keeps the output bounded (ADR-007) and the strategy honest
#: (every edge is derivable from ``bin_count`` + ``lower_edge``/``upper_edge``
#: stamped in ``method_params``). 10 bins is the MVP chart default — a
#: metadata-free heuristic, no per-domain branch (AGENTS §5).
HISTOGRAM_BIN_COUNT = 10

#: ENGINE §3 / §5.1 ``outlier_scan``: candidate fence is the classic Tukey
#: IQR rule — value outside ``[Q1 - k*IQR, Q3 + k*IQR]``. k=1.5 is the
#: standard ("far out") multiplier; recorded in ``method_params`` so the
#: detector id ``iqr_1_5`` in ``OutlierCandidates.method`` is reproducible.
OUTLIER_IQR_MULTIPLIER = 1.5

#: Cap on outlier ``sample_row_refs`` — CONTRACTS §5.4 payload limit and
#: ENGINE §5.2 ``sample_row_ids`` cap (≤200). Row *references*, never raw
#: values (SECURITY §4 masking).
OUTLIER_SAMPLE_REF_CAP = 200

#: Boundedness ceilings for the temporal volume/gap enumeration (ADR-007 —
#: never ship an unbounded series). Above the cap the result is flagged
#: ``truncated`` rather than silently cut.
VOLUME_MAX_BUCKETS = 1_000
GAPS_MAX_PERIODS = 1_000

#: Automatic granularity for volume/gaps (ENGINE §3 "volume per
#: granularity" — MVP has no config knob for it, so pick from the observed
#: span; a heuristic, not a per-domain path). day ≤ 31d, month ≤ ~2y, else
#: year.
_GRANULARITY_DAY_SPAN = 31
_GRANULARITY_MONTH_SPAN = 730
_INTERVAL_BY_GRANULARITY = {
    "hour": "1 hour",
    "day": "1 day",
    "week": "1 week",
    "month": "1 month",
    "year": "1 year",
}

#: Regex for a trailing ``+HH``/``+HH:MM`` UTC offset in an ISO datetime
#: string (timezone_source derivation for VARCHAR-stored temporal).
_OFFSET_RE = re.compile(r"([+-]\d{2}):?(?:\d{2})$")

#: ENGINE §3 note: out-of-range needs a *declared* expected bound; the MVP
#: dataset config (§9) carries no temporal expected-range field, so the
#: metric is honestly ``not_computed`` (never a silent null, AGENTS §3).
_OUT_OF_RANGE_REASON = (
    "no expected temporal range declared in dataset config; "
    "ENGINE §3 out_of_range requires a declared bound (MVP §9 has none)"
)


@dataclass
class _DistTemporalSlot:
    """Mutable per-column assembly bucket for the M2-T6 pass. A column is
    either numeric (distribution decisions) or temporal (temporal decisions)
    — the registry gates the two families on disjoint logical types, so a
    slot never fills both halves."""

    # --- distribution ---
    dist_active: bool = False          # ≥1 distribution decision executed (section on)
    summary: Optional[Tuple[int, Any, Any, Any, Any]] = None  # (n, lo, hi, q1, q3)
    histogram: Optional[Histogram] = None
    skewness: Optional[MetricValue] = None
    outlier: Optional[OutlierCandidates] = None

    # --- temporal ---
    temporal_active: bool = False
    range_min: Optional[str] = None
    range_max: Optional[str] = None
    granularity: Optional[str] = None
    interval: Optional[str] = None
    volume: Optional[Dict[str, Any]] = None
    gaps: Optional[Dict[str, Any]] = None
    out_of_range: Optional[Dict[str, Any]] = None
    timezone_source: Optional[str] = None


def _not_computed_provenance(reason: str) -> Provenance:
    """A provenance-only carrier (histogram / open temporal dicts) at
    not_computed — reason non-empty (schema minLength=1)."""
    return Provenance(method=MetricMethod.NOT_COMPUTED, reason=reason or "not computed")


def _provenance_with_params(
    decision: PlanDecision, extra: Dict[str, Any]
) -> Provenance:
    """Stamp the planner's method/label verbatim, then merge the M2-T6
    execution parameters (bin strategy / IQR multiplier) into
    ``method_params`` — never overwrite an algorithm/seed/strategy the
    planner already recorded (ADR-015/§2.2 honesty lives upstream)."""
    kw = decision.provenance_kwargs()
    params = dict(kw.get("method_params") or {})
    for key, value in extra.items():
        params.setdefault(key, _normalize(value))
    if params:
        kw["method_params"] = params
    return Provenance(**kw)


def _record_metric_error(
    errors: List[RunErrorEntry], metric: str, column: str, exc: Exception
) -> None:
    """§6.4 partial failure: one bounded, non-echoing error entry, run
    continues (siblings keep executing)."""
    errors.append(RunErrorEntry(
        code=ErrorCode.METRIC_NOT_COMPUTED.value,
        task=f"{metric}:{column}",
        message=f"metric execution failed: {_reason(exc)}",
    ))


def _build_numeric_summary_sql(view: str, column: str) -> str:
    """One bounded scalar aggregate feeding histogram binning + IQR fences.
    NULLs are excluded by every aggregate (count/min/max/quantile ignore
    NULL), so ``n`` is the non-null count the bins must sum to."""
    col = _quote_ident(column)
    return (
        f"SELECT count({col}), min({col}), max({col}), "
        f"quantile_cont({col}, 0.25), quantile_cont({col}, 0.75) "
        f"FROM {_quote_ident(view)}"
    )


def _build_bucket_counts_sql(view: str, column: str, lo: float, hi: float, nb: int) -> str:
    """width_bucket emulation over ``[lo, hi]`` (DuckDB has no width_bucket,
    STATE gotcha): bucket index ``1..nb`` with the top edge folded into the
    last bin. ``lo``/``hi`` are engine-computed numeric literals (never raw
    user data), so interpolating them is safe; the row filter drops NULLs."""
    col = _quote_ident(column)
    expr = (
        f"CAST(LEAST({int(nb)}, FLOOR(({col} - {lo!r}) / "
        f"({hi!r} - {lo!r}) * {int(nb)}) + 1) AS INTEGER)"
    )
    return (
        f"SELECT {expr} AS bucket, count(*) FROM {_quote_ident(view)} "
        f"WHERE {col} IS NOT NULL GROUP BY bucket ORDER BY bucket"
    )


def _build_outlier_count_sql(view: str, column: str, lower: float, upper: float) -> str:
    col = _quote_ident(column)
    return (
        f"SELECT count(*) FILTER (WHERE {col} < {lower!r} OR {col} > {upper!r}) "
        f"FROM {_quote_ident(view)} WHERE {col} IS NOT NULL"
    )


def _build_outlier_refs_sql(
    view: str, column: str, row_id: str, lower: float, upper: float, cap: int
) -> str:
    col = _quote_ident(column)
    rid = _quote_ident(row_id)
    return (
        f"SELECT {rid} FROM {_quote_ident(view)} WHERE {col} IS NOT NULL AND "
        f"({col} < {lower!r} OR {col} > {upper!r}) ORDER BY {rid} LIMIT {int(cap)}"
    )


def _build_volume_sql(view: str, column: str, granularity: str, cap: int) -> str:
    """Row volume per bucket at ``granularity``. The period key is CAST to
    VARCHAR on fetch — a raw TIMESTAMP WITH TIME ZONE scalar needs pytz
    (STATE gotcha); the truncated instant is always a plain timestamp/date
    but stays string-safe regardless of source type."""
    col = _quote_ident(column)
    return (
        f"SELECT CAST(date_trunc('{granularity}', {col}) AS VARCHAR) AS period, "
        f"count(*) FROM {_quote_ident(view)} WHERE {col} IS NOT NULL "
        f"GROUP BY period ORDER BY period LIMIT {int(cap) + 1}"
    )


def _build_expected_periods_sql(
    view: str, column: str, granularity: str, interval: str
) -> str:
    """The closed interval of expected periods ``[trunc(min), trunc(max)]``
    stepped by ``interval``; gaps = expected minus observed. ``generate_series``
    emits timestamps → CAST to VARCHAR keeps the fetch pytz-free (STATE gotcha)."""
    col = _quote_ident(column)
    return (
        f"WITH b AS (SELECT date_trunc('{granularity}', min({col})) a, "
        f"date_trunc('{granularity}', max({col})) z FROM {_quote_ident(view)}) "
        f"SELECT CAST(unnest(generate_series(a, z, INTERVAL '{interval}')) AS VARCHAR) "
        f"AS period FROM b WHERE a IS NOT NULL AND z IS NOT NULL "
        f"LIMIT {int(GAPS_MAX_PERIODS) + 1}"
    )


def _pick_granularity(span_days: Optional[int]) -> Tuple[str, str]:
    """Heuristic granularity from the observed span (MVP: no config knob).
    Unparseable/empty span defaults to ``month``."""
    if span_days is None:
        return "month", _INTERVAL_BY_GRANULARITY["month"]
    if span_days <= _GRANULARITY_DAY_SPAN:
        return "day", _INTERVAL_BY_GRANULARITY["day"]
    if span_days <= _GRANULARITY_MONTH_SPAN:
        return "month", _INTERVAL_BY_GRANULARITY["month"]
    return "year", _INTERVAL_BY_GRANULARITY["year"]


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Lenient ISO parse of a temporal scalar already rendered to VARCHAR.
    Handles the trailing ``Z`` and a ``+HH``/``+HH:MM`` offset; falls back to
    the leading date when the datetime part fails ``fromisoformat``."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text[:10])
        except ValueError:
            return None


def _derive_timezone_source(physical: str, sample: Optional[str]) -> str:
    """CONTRACTS §3.2 ``timezone_source`` ∈ ``naive|utc|offset:+HH``. DuckDB
    stores ``TIMESTAMPTZ`` as a UTC instant (→ ``utc``); a bare ``TIMESTAMP``
    or ``DATE`` is zone-less (→ ``naive``); a VARCHAR-stored temporal is
    sniffed from the sample string (trailing ``Z`` → utc, an explicit offset
    → ``offset:+HH``, else naive)."""
    p = (physical or "").upper()
    if "TIME ZONE" in p or p.startswith("TIMESTAMPTZ"):
        return "utc"
    if p.startswith("TIMESTAMP"):
        return "naive"
    if p == "DATE" or p.startswith("TIME"):
        return "naive"
    text = (sample or "").strip()
    if text.endswith("Z"):
        return "utc"
    m = _OFFSET_RE.search(text)
    if m:
        return f"offset:{m.group(1)}"  # group(1) == +HH
    return "naive"


def _compute_distribution(
    con: duckdb.DuckDBPyConnection,
    slot: _DistTemporalSlot,
    view: str,
    column: str,
    phys: str,
    row_id: Optional[str],
    decision: PlanDecision,
    reg: MetricRegistry,
    errors: List[RunErrorEntry],
) -> None:
    """Resolve one distribution decision (histogram / skewness / outlier). The
    base summary (count/min/max/q1/q3) is computed once per column and cached
    on the slot. Built SQL (histogram, outliers) always executes the exact
    form; only the Provenance label follows the planner's method — the same
    approximate-without-approx-template delegation M2-T4/T5 established."""
    metric = decision.metric_id

    if metric == "distribution.skewness":
        spec = reg.get(metric)
        # an `approximate` skewness decision (quick) has no approx template →
        # render the EXACT SQL but keep the planner's approximate label.
        sql_method = decision.method
        if decision.method is MetricMethod.APPROXIMATE and not spec.approx_sql_template:
            sql_method = MetricMethod.EXACT
        try:
            value = _scalar(con, render_sql(
                spec, view, column=column, physical_type=phys, method=sql_method))
        except duckdb.Error as exc:
            _record_metric_error(errors, metric, column, exc)
            slot.skewness = _not_computed_value(f"metric execution failed: {_reason(exc)}")
            return
        if value is None:
            # undefined skewness (< 3 non-null / zero variance ⇒ NULL): an
            # honest not_computed, never a silent-null MetricValue (AGENTS §3).
            slot.skewness = _not_computed_value(
                "skewness undefined (fewer than 3 non-null values or zero variance)")
        else:
            slot.skewness = _metric_value(value, decision)
        return

    # histogram / outlier_candidates share one bounded numeric summary.
    if slot.summary is None:
        try:
            row = con.execute(_build_numeric_summary_sql(view, column)).fetchone()
        except duckdb.Error as exc:
            _record_metric_error(errors, metric, column, exc)
            return
        n, lo, hi, q1, q3 = row
        slot.summary = (int(n or 0), _normalize(lo), _normalize(hi), _normalize(q1), _normalize(q3))
    n, lo, hi, q1, q3 = slot.summary

    if metric == "distribution.histogram":
        if n == 0 or lo is None or hi is None:
            slot.histogram = Histogram(
                bins=[], counts=[],
                provenance=_not_computed_provenance("no non-null values to bin"))
            return
        if float(hi) == float(lo):
            # constant column: a single bin (the width formula would divide by
            # zero); honest and bounded.
            slot.histogram = Histogram(
                bins=[float(lo)], counts=[n],
                provenance=_provenance_with_params(
                    decision, {"bin_strategy": "fixed_width", "bin_count": 1,
                               "lower_edge": lo, "upper_edge": hi}))
            return
        nb = HISTOGRAM_BIN_COUNT
        try:
            rows = _fetch_rows(con, _build_bucket_counts_sql(
                view, column, float(lo), float(hi), nb))
        except duckdb.Error as exc:
            _record_metric_error(errors, metric, column, exc)
            return
        counts = [0] * nb
        for bucket, cnt in rows:
            idx = int(bucket) - 1
            if 0 <= idx < nb:
                counts[idx] = int(cnt)
        step = (float(hi) - float(lo)) / nb
        bins = [float(lo) + i * step for i in range(nb)]
        slot.histogram = Histogram(
            bins=bins, counts=counts,
            provenance=_provenance_with_params(
                decision, {"bin_strategy": "fixed_width", "bin_count": nb,
                           "lower_edge": lo, "upper_edge": hi}))
        return

    # distribution.outlier_candidates
    if n == 0 or q1 is None or q3 is None:
        return  # nothing to fence ⇒ omit the sub-block (never a fabricated 0)
    iqr = float(q3) - float(q1)
    lower = float(q1) - OUTLIER_IQR_MULTIPLIER * iqr
    upper = float(q3) + OUTLIER_IQR_MULTIPLIER * iqr
    try:
        count = int(_scalar(con, _build_outlier_count_sql(view, column, lower, upper)) or 0)
        refs: List[str] = []
        if count and row_id is not None:
            ref_rows = _fetch_rows(con, _build_outlier_refs_sql(
                view, column, row_id, lower, upper, OUTLIER_SAMPLE_REF_CAP))
            refs = [f"row:{r[0]}" for r in ref_rows]
    except duckdb.Error as exc:
        _record_metric_error(errors, metric, column, exc)
        return
    prov = _provenance_with_params(
        decision, {"iqr_multiplier": OUTLIER_IQR_MULTIPLIER, "quantile_method": "quantile_cont"})
    kwargs: Dict[str, Any] = {"count": count, "method": "iqr_1_5", "provenance": prov}
    if refs:
        kwargs["sample_row_refs"] = refs
    slot.outlier = OutlierCandidates(**kwargs)


def _compute_temporal(
    con: duckdb.DuckDBPyConnection,
    slot: _DistTemporalSlot,
    view: str,
    column: str,
    phys: str,
    decision: PlanDecision,
    reg: MetricRegistry,
    errors: List[RunErrorEntry],
) -> None:
    """Resolve one temporal decision. ``range`` endpoints are templated
    (render_sql wraps a CAST AS VARCHAR on the fetch for TZ-safe probes);
    volume/gaps are built here over the observed/expected period series; and
    ``out_of_expected_range`` is honestly ``not_computed`` (no config bound)."""
    metric = decision.metric_id

    if metric in ("temporal.range_min", "temporal.range_max"):
        spec = reg.get(metric)
        try:
            value = _scalar(con, render_sql(
                spec, view, column=column, physical_type=phys))
        except duckdb.Error as exc:
            _record_metric_error(errors, metric, column, exc)
            return
        text = None if value is None else str(value)
        if metric == "temporal.range_min":
            slot.range_min = text
        else:
            slot.range_max = text
        return

    # granularity depends on the resolved range span.
    if slot.granularity is None:
        lo_dt = _parse_iso(slot.range_min)
        hi_dt = _parse_iso(slot.range_max)
        span = (hi_dt - lo_dt).days if (lo_dt and hi_dt) else None
        slot.granularity, slot.interval = _pick_granularity(span)
    granularity = slot.granularity or "month"
    interval = slot.interval or _INTERVAL_BY_GRANULARITY[granularity]

    if metric == "temporal.volume_per_granularity":
        try:
            rows = _fetch_rows(con, _build_volume_sql(
                view, column, granularity, VOLUME_MAX_BUCKETS))
        except duckdb.Error as exc:
            _record_metric_error(errors, metric, column, exc)
            return
        truncated = len(rows) > VOLUME_MAX_BUCKETS
        buckets = rows[:VOLUME_MAX_BUCKETS]
        entry: Dict[str, Any] = {
            "granularity": granularity,
            "buckets": [{"period": str(p), "count": int(c)} for p, c in buckets],
            # open Temporal dict → plain JSON (a nested Provenance model is not
            # serialisable by ContractModel.model_dump(mode="json"))
            "provenance": _provenance_with_params(
                decision, {"granularity": granularity}).model_dump(mode="json"),
        }
        if truncated:
            entry["truncated"] = True
        slot.volume = entry
        return

    if metric == "temporal.gaps":
        # a decision reaching here was NOT structurally skipped (not_computed
        # is handled by the caller) — compute missing periods over the view.
        try:
            observed = {str(p) for (p, _c) in _fetch_rows(
                con, _build_volume_sql(view, column, granularity, VOLUME_MAX_BUCKETS))}
            expected_rows = _fetch_rows(
                con, _build_expected_periods_sql(view, column, granularity, interval))
        except duckdb.Error as exc:
            _record_metric_error(errors, metric, column, exc)
            return
        expected = [str(r[0]) for r in expected_rows]
        truncated = len(expected) > GAPS_MAX_PERIODS
        missing = [p for p in expected[:GAPS_MAX_PERIODS] if p not in observed]
        entry = {
            "granularity": granularity,
            "missing_periods": missing,
            "count": len(missing),
            "provenance": _provenance_with_params(
                decision, {"granularity": granularity}).model_dump(mode="json"),
        }
        if truncated:
            entry["truncated"] = True
        slot.gaps = entry
        return

    # temporal.out_of_range — MVP has no declared expected range input.
    slot.out_of_range = {
        "provenance": _not_computed_provenance(_OUT_OF_RANGE_REASON).model_dump(mode="json")
    }


def execute_distribution_and_temporal(
    con: duckdb.DuckDBPyConnection,
    handle: RelationHandle,
    report: DiscoveryReport,
    plan: ExecutionPlan,
    config: Optional[DatasetConfig] = None,
    registry: Optional[MetricRegistry] = None,
) -> ProfilerResult:
    """Run every distribution + single-column temporal decision in ``plan``
    and return updated ColumnProfiles (family scope
    :data:`DISTRIBUTION_TEMPORAL_FAMILIES`; other families are ignored —
    each M2-T4..T6 runner owns its slice).

    Contract surface (ENGINE §3):
      - ``ColumnProfile.distribution`` — :class:`Distribution`(histogram,
        skewness, outlier_candidates). Emitted only when at least one
        distribution decision executed for the column; a §4 gate-2 section
        disable marks every decision not_computed and the whole block stays
        absent (never a fabricated empty distribution).
      - ``ColumnProfile.temporal`` — :class:`Temporal`(range,
        volume_per_granularity, gaps, out_of_expected_range, timezone_source).
        Same section discipline; unset fields are DROPPED (open-model
        null-rejection gotcha, M2-T5).

    Honesty rules honoured (ADR-015 / §6.4 / AGENTS §3):
      - histogram bin strategy recorded in ``method_params`` (§2.2);
        skewness that is undefined (NULL) ⇒ not_computed + reason, never a
        silent-null MetricValue.
      - ``out_of_expected_range`` is ``not_computed`` + reason because the
        MVP dataset config declares no expected temporal bound (§3 note);
        it is never guessed from the observed range.
      - temporal fetches CAST to VARCHAR (no pytz, STATE gotcha).
      - ``approximate`` distribution decisions (quick mode) execute the exact
        built SQL but keep the planner's label — the M2-T3→T4 delegation. A
        ``sampled`` temporal decision (standard + sampling) runs the identical
        built SQL against the one shared bounded sample.
      - a per-task ``duckdb.Error`` ⇒ :class:`RunErrorEntry`
        (``METRIC_NOT_COMPUTED``) + an honest absence; siblings keep running.
    """
    reg = registry if registry is not None else default_registry()
    errors: List[RunErrorEntry] = []

    rows: List[Tuple[str, PlanDecision]] = []
    for cp in plan.column_plans:
        for d in cp.decisions:
            if reg.get(d.metric_id).family in DISTRIBUTION_TEMPORAL_FAMILIES:
                rows.append((cp.column.profile.column, d))

    # --- sampling window (shared bounded relation, same policy as M2-T4/T5) --
    sampling = config.profiling.sampling if config else None
    sample_view: Optional[str] = None
    if any(d.method is MetricMethod.SAMPLED for _c, d in rows):
        if not (sampling and sampling.enabled and sampling.size and sampling.seed is not None):
            raise ValueError(
                "plan carries sampled distribution/temporal decisions but no "
                "sampling window is configured"
            )
        sample_view = _sample_relation(
            con, handle.view_name, sampling.strategy or "reservoir",
            sampling.size, sampling.seed,
        )

    physical_of: Dict[str, str] = dict(handle.columns)
    slots: Dict[str, _DistTemporalSlot] = {}

    def _slot(column: str) -> _DistTemporalSlot:
        return slots.setdefault(column, _DistTemporalSlot())

    for column, decision in rows:
        family = reg.get(decision.metric_id).family
        slot = _slot(column)
        phys = physical_of.get(column, "")
        view = sample_view if decision.method is MetricMethod.SAMPLED else handle.view_name

        if decision.method is MetricMethod.NOT_COMPUTED:
            # structurally skipped (§4 gate 2 / deep full-relation refusal): the
            # block is omitted unless a sibling decision executed.
            continue

        if family == "distribution":
            slot.dist_active = True
            _compute_distribution(
                con, slot, view, column, phys, handle.row_id_column, decision, reg, errors)
        else:
            slot.temporal_active = True
            _compute_temporal(con, slot, view, column, phys, decision, reg, errors)

    # --- assemble blocks -----------------------------------------------------
    profiles: List[ColumnProfile] = []
    for cd in report.columns:
        profile = cd.profile
        slot = slots.get(profile.column)
        update: Dict[str, Any] = {}
        if slot is not None and slot.dist_active:
            dk: Dict[str, Any] = {}
            if slot.histogram is not None:
                dk["histogram"] = slot.histogram
            if slot.skewness is not None:
                dk["skewness"] = slot.skewness
            if slot.outlier is not None:
                dk["outlier_candidates"] = slot.outlier
            update["distribution"] = Distribution(**dk) if dk else Distribution()
        if slot is not None and slot.temporal_active:
            tk: Dict[str, Any] = {}
            if slot.range_min is not None or slot.range_max is not None:
                rk: Dict[str, Any] = {}
                if slot.range_min is not None:
                    rk["min"] = slot.range_min
                if slot.range_max is not None:
                    rk["max"] = slot.range_max
                tk["range"] = TemporalRange(**rk)
            if slot.volume is not None:
                tk["volume_per_granularity"] = slot.volume
            if slot.gaps is not None:
                tk["gaps"] = slot.gaps
            if slot.out_of_range is not None:
                tk["out_of_expected_range"] = slot.out_of_range
            # timezone_source derived from the resolved physical type + range
            # sample (a metadata signal, not a registry metric decision).
            phys = physical_of.get(profile.column, "")
            sample = slot.range_min or slot.range_max
            tk["timezone_source"] = _derive_timezone_source(phys, sample)
            update["temporal"] = Temporal(**tk) if tk else Temporal()
        profiles.append(profile.model_copy(update=update) if update else profile)

    return ProfilerResult(tuple(profiles), tuple(errors), sample_view)
