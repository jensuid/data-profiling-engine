"""Execution planner (M2-T3, ENGINE §2 / §2.1 / §8; ARCHITECTURE §3
``engine/planner.py`` — "exact / approximate / sampled decision").

The planner decides, per metric × column, **how** to compute given mode,
size and sampling config (ENGINE §2 flowchart: plan DuckDB scan → size vs
memory + mode → exact where cheap / approximate or sampled where allowed).
It never computes anything itself — it emits :class:`PlanDecision` rows that
the M2-T4..T6 profilers execute, stamping ``method`` + ``method_params`` into
contract Provenance verbatim (ADR-015). Sampling honesty (§2.2 warning) is
enforced *here*, structurally: every non-exact decision carries its
parameters (algorithm / strategy / seed) or a `reason` string, and a
`sampled` decision is emitted only together with a seed.

Decision table (ENGINE §2.1 "Default at large N" × §8 mode matrix):

- ``cheap_exact``      → exact always; a sampling window never degrades it
                          (exact_under_sampling rows recompute on the full
                          relation; other rows run on the sample labeled
                          `sampled`).
- ``moderate_exact``   → exact under standard/deep; approximate under quick
                          when the metric supports it, else sampled/not_computed.
- ``expensive_exact``  → exact under deep always; under quick/standard:
                          approximate when large or sketch supported,
                          sampled when a sampling window is configured,
                          else exact (small N).
- ``prohibited_at_scale`` → never exact; sampled with a labeled cap when a
                          sampling window exists, else not_computed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from data_profiling_engine.discovery.types import ColumnDiscovery, DiscoveryReport
from data_profiling_engine.engine.metrics import (
    LARGE_ROW_LIMIT,
    CostClass,
    MetricRegistry,
    MetricSpec,
    default_registry,
)
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.schemas.models import (
    ConfigSampling,
    MetricMethod,
    ProfileMode,
)

#: ENGINE §8 quick mode: "heavy approximate/sampled, all labeled".
QUICK_MODE = ProfileMode.QUICK
DEEP_MODE = ProfileMode.DEEP

#: Not-computed reason prefixes are free text, but every emitted metric's
#: Provenance.reason must be non-empty (schema minLength=1) — the planner
#: guarantees that via :meth:`PlanDecision.invariants_ok`.


class PlanningError(Exception):
    """Plan-construction failure carrying the registered §8 engine-layer code
    ``METRIC_NOT_COMPUTED`` (an un-plannable metric/request is exactly what
    that row describes). A bad plan surfaces before any compute is
    scheduled — invalid config rejected before profiling (AGENTS §5)."""

    code = ErrorCode.METRIC_NOT_COMPUTED

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PlanDecision:
    """One planned metric execution. ``method`` maps onto the contract
    ``MetricMethod`` of the same value; when ``not_computed`` the decision
    instead becomes a Provenance with ``method=not_computed`` + ``reason``
    (AGENTS §3 — absent values are not_computed + reason, never a silent
    null), and the profiler task is skipped."""

    metric_id: str
    column: Optional[str]          # None for dataset-scope decisions
    method: MetricMethod
    reason: Optional[str] = None   # required iff method is not_computed
    params: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.method is MetricMethod.NOT_COMPUTED and not self.reason:
            raise PlanningError(
                f"{self.metric_id}: not_computed requires a non-empty reason"
            )
        if self.method is MetricMethod.SAMPLED and self.params.get("seed") is None:
            raise PlanningError(
                f"{self.metric_id}: sampled requires a seed (ADR-015)"
            )
        if self.method is MetricMethod.APPROXIMATE and not self.params.get("algorithm"):
            raise PlanningError(
                f"{self.metric_id}: approximate requires algorithm recorded in params (§2.2)"
            )

    def provenance_kwargs(self) -> Dict[str, object]:
        """Shape directly consumable by ``Provenance(method=..., method_params=
        ..., reason=...)``; method_params omitted when empty (never a silent
        null on the contract surface)."""
        out: Dict[str, object] = {"method": self.method.value}
        if self.params:
            out["method_params"] = dict(self.params)
        if self.reason is not None:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class ColumnPlan:
    """Per-column plan bundle: the discovery row plus its applicable
    decisions in catalog order."""

    column: ColumnDiscovery
    decisions: Tuple[PlanDecision, ...]


@dataclass(frozen=True)
class ExecutionPlan:
    """Full plan for one run: dataset-scope tasks first, then per-column."""

    mode: ProfileMode
    n_rows: int
    dataset_decisions: Tuple[PlanDecision, ...]
    column_plans: Tuple[ColumnPlan, ...]

    def computed_tasks(self) -> Tuple[PlanDecision, ...]:
        """Decisions that must actually execute (everything except
        not_computed), dataset-scope first."""
        out = [d for d in self.dataset_decisions if d.method is not MetricMethod.NOT_COMPUTED]
        for cp in self.column_plans:
            out.extend(d for d in cp.decisions if d.method is not MetricMethod.NOT_COMPUTED)
        return tuple(out)

    def skipped(self) -> Tuple[PlanDecision, ...]:
        out = [d for d in self.dataset_decisions if d.method is MetricMethod.NOT_COMPUTED]
        for cp in self.column_plans:
            out.extend(d for d in cp.decisions if d.method is MetricMethod.NOT_COMPUTED)
        return tuple(out)


def _sampling_params(sampling: ConfigSampling) -> Dict[str, object]:
    """§2.2: strategy + seed always recorded for sampled results."""
    return {
        "strategy": sampling.strategy,
        "sample_size": sampling.size,
        "seed": sampling.seed,
    }


def _q(ident: str) -> str:
    """Double-quote a DuckDB identifier (same policy as discovery.signals)."""
    return '"' + ident.replace('"', '""') + '"'


def render_sql(
    spec: MetricSpec,
    view_name: str,
    column: Optional[str] = None,
    physical_type: Optional[str] = None,
    method: MetricMethod = MetricMethod.EXACT,
) -> str:
    """Materialise a registry template for execution (the single place SQL is
    rendered; profilers M2-T4..T6 execute exactly this string).

    ``method == approximate`` swaps in ``approx_sql_template`` (HLL/t-digest,
    ENGINE §2.2); ``method == sampled`` runs the *same* SQL against the
    bounded sample relation — the honesty lives in the Provenance label, not
    a different query. Temporal scalars get CAST(... AS VARCHAR) on fetch
    unless the source column is already a string (DuckDB TZ probe needs
    pytz, which we do not ship — gotcha)."""
    if not spec.sql_template:
        raise PlanningError(f"{spec.metric_id} has no sql_template; profiler task builds it")
    template = spec.sql_template
    if method is MetricMethod.APPROXIMATE:
        if not spec.approx_sql_template:
            raise PlanningError(f"{spec.metric_id} declares no approx template")
        template = spec.approx_sql_template
    col_sql = _q(column or "")
    if spec.cast_varchar_on_fetch and (physical_type or "").upper() not in ("VARCHAR", "TEXT"):
        # wrap the column reference itself — every templated cast-metric is an
        # aggregate over exactly one {col} occurrence
        col_sql = f"CAST({_q(column or '')} AS VARCHAR)"
    return template.replace("{table}", _q(view_name)).replace("{col}", col_sql)


def decide_method(
    spec: MetricSpec,
    mode: ProfileMode,
    n_rows: int,
    sampling: Optional[ConfigSampling] = None,
) -> Tuple[MetricMethod, Dict[str, object], Optional[str]]:
    """Pure decision function for one metric × size × mode × sampling window.

    Split out from :func:`plan_execution` so the §2.1/§8 decision table is
    reference-testable without a relation (TESTING §3.1 covers the table as
    a behavioural contract, not only the SQL).
    """
    sampling_on = bool(sampling and sampling.enabled)
    large = n_rows > LARGE_ROW_LIMIT
    cc = spec.cost_class

    if cc is CostClass.PROHIBITED_AT_SCALE:
        # §2.1: "never full; sample/cap with labeling"
        if sampling_on:
            return MetricMethod.SAMPLED, _sampling_params(sampling), None  # type: ignore[arg-type]
        return (
            MetricMethod.NOT_COMPUTED,
            {},
            f"{spec.metric_id} is prohibited at full scale (ENGINE §2.1); "
            "enable sampling to compute it on a labeled subset",
        )

    if cc is CostClass.CHEAP_EXACT:
        if sampling_on and not spec.exact_under_sampling and not spec.requires_full_relation:
            # cheap but only meaningful within the configured window: run on
            # the sample, labeled honestly (§2.2 sampling honesty)
            return MetricMethod.SAMPLED, _sampling_params(sampling), None  # type: ignore[arg-type]
        return MetricMethod.EXACT, {}, None

    # exact-first override for sketchable metrics
    def _sketch(params: Dict[str, object]) -> Tuple[MetricMethod, Dict[str, object], Optional[str]]:
        if spec.supports_approximate and spec.approx_sql_template:
            params.setdefault("algorithm", "t-digest" if "quantile" in (spec.approx_sql_template or "") else "sketch")
            return MetricMethod.APPROXIMATE, params, None
        if spec.supports_approximate:
            # sketchable at contract level; execution detail owned by the
            # profiler task (M2-T4..T6) which records the algorithm name
            params.setdefault("algorithm", spec.metric_id.rsplit(".", 1)[-1])
            return MetricMethod.APPROXIMATE, params, None
        if sampling_on:
            return MetricMethod.SAMPLED, _sampling_params(sampling), None  # type: ignore[arg-type]
        return (
            MetricMethod.NOT_COMPUTED,
            {},
            f"{spec.metric_id} cannot be approximated and full computation "
            f"is gated out at {mode.value} mode for this metric class",
        )

    if cc is CostClass.MODERATE_EXACT:
        if mode is QUICK_MODE:
            return _sketch({})
        return MetricMethod.EXACT, {}, None

    # EXPENSIVE_EXACT
    if mode is DEEP_MODE:
        # §8 deep: "expensive ops gated" but exact when feasible; a full
        # relation is required (sampled distinct would misstate cardinality)
        if sampling_on and spec.requires_full_relation:
            return (
                MetricMethod.NOT_COMPUTED,
                {},
                f"{spec.metric_id} needs the full relation (deep mode) but a "
                "sampling window is configured — refusing to label a sample as full-scale",
            )
        if sampling_on:
            return MetricMethod.SAMPLED, _sampling_params(sampling), None  # type: ignore[arg-type]
        return MetricMethod.EXACT, {"mode": mode.value}, None
    if sampling_on:
        return MetricMethod.SAMPLED, _sampling_params(sampling), None  # type: ignore[arg-type]
    if large:
        # §2.1 "Default at large N" gates; small-N expensive stays exact
        # under standard (§8: "exact where cheap" — a full DISTINCT hash over
        # ≤ LARGE_ROW_LIMIT rows is cheap even if the metric class is heavy).
        if spec.approx_sql_template:
            return (
                MetricMethod.APPROXIMATE,
                {"algorithm": "HLL" if "approx_count_distinct" in spec.approx_sql_template else "sketch"},
                None,
            )
        return _sketch({})
    if mode is QUICK_MODE:
        # §8 quick applies the degrade path regardless of size (labeled)
        return _sketch({})
    return MetricMethod.EXACT, {}, None


def plan_execution(
    report: DiscoveryReport,
    mode: ProfileMode,
    n_rows: int,
    sampling: Optional[ConfigSampling] = None,
    sections: Optional[List[str]] = None,
    registry: Optional[MetricRegistry] = None,
) -> ExecutionPlan:
    """Build the full execution plan for one run.

    ENGINE §4 gates applied: (1) type applicability via
    :meth:`MetricRegistry.metrics_for_column`, (2) dataset-config sections
    (``profiling.sections``; ``None`` = all sections active), (3) mode/cost
    class via :func:`decide_method`. Gates 4–6 (metadata, connector
    capability, budgets) feed M2-T7/M2-T10; a budget breach will reuse the
    same not_computed + reason channel (§4 gate 6).
    """
    reg = registry if registry is not None else default_registry()
    active_sections = set(sections) if sections is not None else None

    dataset_decisions: List[PlanDecision] = []
    for spec in reg.dataset_metrics():
        if active_sections is not None and spec.section not in active_sections:
            dataset_decisions.append(
                PlanDecision(spec.metric_id, None, MetricMethod.NOT_COMPUTED,
                             reason=f"section {spec.section!r} disabled by dataset config (ENGINE §4 gate 2)")
            )
            continue
        method, params, reason = decide_method(spec, mode, n_rows, sampling)
        dataset_decisions.append(PlanDecision(spec.metric_id, None, method, reason, params))

    column_plans: List[ColumnPlan] = []
    for column in report.columns:
        decisions: List[PlanDecision] = []
        for spec in reg.metrics_for_column(column):
            if active_sections is not None and spec.section not in active_sections:
                decisions.append(
                    PlanDecision(spec.metric_id, column.profile.column,
                                 MetricMethod.NOT_COMPUTED,
                                 reason=f"section {spec.section!r} disabled by dataset config (ENGINE §4 gate 2)")
                )
                continue
            method, params, reason = decide_method(spec, mode, n_rows, sampling)
            decisions.append(
                PlanDecision(spec.metric_id, column.profile.column, method, reason, params)
            )
        column_plans.append(ColumnPlan(column, tuple(decisions)))

    return ExecutionPlan(mode, n_rows, tuple(dataset_decisions), tuple(column_plans))
