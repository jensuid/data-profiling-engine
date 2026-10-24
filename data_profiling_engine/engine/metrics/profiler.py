"""Statistical + Missingness profiler runner (M2-T4, ENGINE §3; TESTING §3.1).

Executes the M2-T3 plan: consumes an :class:`ExecutionPlan`, renders each
templated decision through :func:`render_sql` (the single SQL materialisation
point) against the bound DuckDB relation, and stamps the scalars into
``ColumnProfile.stats`` / ``ColumnProfile.missingness`` as contract
:class:`MetricValue` objects carrying
``Provenance(**decision.provenance_kwargs())`` verbatim (ADR-015 — sampled
needs seed, approximate needs algorithm, not_computed needs reason and NO
value).

Obligations honoured here:

- **Never a full materialisation** (ADR-007): every metric is a one-row
  scalar aggregate over the DuckDB view; the only extra relation ever built
  is the bounded sample (≤ ``sampling.size`` rows) and a 1-column sentinel
  relation.
- **Sampling honesty** (ENGINE §2.2): a ``sampled`` decision runs the same
  SQL against one shared bounded sample relation; the label + strategy +
  size + seed live in the Provenance, never in a different query. The
  bounded-hash policy is a preliminary mechanism — the reservoir + seed
  finalisation is M2-T10.
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
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

import duckdb

from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery.types import DiscoveryReport
from data_profiling_engine.engine.metrics import MetricRegistry, default_registry
from data_profiling_engine.engine.planner import (
    ExecutionPlan,
    PlanDecision,
    render_sql,
)
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.schemas.models import (
    ColumnProfile,
    DatasetConfig,
    MetricMethod,
    MetricValue,
    Missingness,
    NumericStats,
    Provenance,
    RunErrorEntry,
)

#: Families executed by this runner; uniqueness/categorical (M2-T5) and
#: distribution/temporal (M2-T6) decisions in the same plan are left alone.
EXECUTED_FAMILIES: Tuple[str, ...] = ("statistical", "missingness")

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
    """Outcome of one M2-T4 execution pass."""

    profiles: Tuple[ColumnProfile, ...]
    errors: Tuple[RunErrorEntry, ...] = ()
    sample_view: Optional[str] = None  # bounded relation name, None if no sampling


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
    decision (≤ ``size`` rows — ADR-007). Deterministic per
    (view, strategy, size, seed): ``reservoir`` uses a seeded pseudo-random
    ordering (md5 over seed + ``_row_id``) — a preliminary approximation of
    true reservoir sampling, finalised in M2-T10; ``block`` honours locality
    (ENGINE §2.2) via an unordered bounded scan. The name is content-hashed
    so identical windows reuse one relation and never clobber a user object."""
    tag = hashlib.sha256(
        f"{base_view}|{strategy}|{size}|{seed}".encode("utf-8")
    ).hexdigest()[:12]
    name = f"dpe_sample_{tag}"
    if strategy == "block":
        body = f'SELECT * FROM "{base_view}" LIMIT {int(size)}'
    else:
        body = (
            f'SELECT * FROM "{base_view}" '
            f"ORDER BY md5(CONCAT_WS('|', 'seed#{int(seed)}', _row_id::VARCHAR)) "
            f"LIMIT {int(size)}"
        )
    con.execute(f'DROP VIEW IF EXISTS "{name}"')
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
