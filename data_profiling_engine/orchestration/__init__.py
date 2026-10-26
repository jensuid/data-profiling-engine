"""Execution orchestration — the engine spine (M2-T10; ARCHITECTURE §3
``orchestration/`` — "execution plan, job manager, runner, worker entrypoint").

This is the **engine path** the modes + sampling inputs travel through
end-to-end (ENGINE §2 flowchart: plan DuckDB scan → size vs memory + mode →
exact / approximate / sampled → execute → normalize). It wires the pieces the
earlier M2 tasks delivered, in contract order:

    open source → discover → plan (mode + sampling + sections) →
    M2-T4/T5/T6 profiler passes (composed per column) →
    M2-T7 quality rules → M2-T8 findings → M2-T9 recommendations

and returns the assembled evidence in one structured result. It performs **no
new computation** and registers **no new metric** — it only sequences existing
modules so ``config.profiling.mode`` and ``config.profiling.sampling`` reach
the planner and every ``sampled`` metric is stamped with its seed (ADR-015) by
the shared bounded reservoir (M2-T10, ``profiler._sample_relation``).

The §3.1 ``DatasetProfile`` / §2 ``ProfilingRun`` rollup assembly (row_count,
primary_key_candidates, temporal_coverage, timings) is deliberately **not**
done here — that is M2-T11's profiler-cli end-to-end gate; this runner exposes
the assembled ``columns`` plus the sampling record + plan the CLI folds into
those contracts. Persistence / reporting / the worker entrypoint are M3/M5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import duckdb

from data_profiling_engine import duckdb_session
from data_profiling_engine.connectors import get_connector
from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery import discover
from data_profiling_engine.discovery.types import DiscoveryReport
from data_profiling_engine.engine.metrics import MetricRegistry, default_registry
from data_profiling_engine.engine.metrics.profiler import (
    ProfilerResult,
    execute_distribution_and_temporal,
    execute_stat_and_missingness,
    execute_uniqueness_and_categorical,
)
from data_profiling_engine.engine.planner import ExecutionPlan, plan_execution
from data_profiling_engine.findings import build_findings
from data_profiling_engine.quality.executor import run_quality_checks
from data_profiling_engine.recommendations import build_recommendations
from data_profiling_engine.schemas.models import (
    ColumnProfile,
    DatasetConfig,
    Finding,
    ProfileMode,
    QualityCheckResult,
    Recommendation,
    RunErrorEntry,
    RunSampling,
)

#: The metric sub-blocks the M2-T4..T6 profiler passes assemble onto a
#: ColumnProfile. The three passes are family-scoped and touch DISJOINT blocks
#: (T4 stats/missingness, T5 uniqueness/categorical, T6 distribution/temporal),
#: so their outputs can be merged onto the one discovery-skeleton profile per
#: column without conflict — the composition the individual per-family runners
#: (each starting from the discovery base) do not perform themselves.
_MERGED_BLOCKS: Tuple[str, ...] = (
    "stats",
    "missingness",
    "uniqueness",
    "categorical",
    "distribution",
    "temporal",
)


@dataclass(frozen=True)
class ProfilingPipelineResult:
    """Everything one end-to-end engine run produces, before contract rollup.

    ``profiles`` carry every enabled metric family merged per column;
    ``findings`` are the M2-T9 result re-emitted with ``recommendation_ids``
    wired (not the raw M2-T8 findings); ``errors`` is the §6.4 union of every
    per-task failure across the profiler passes and the quality executor (one
    broken metric never aborts the run); ``sampling`` is the §2 reproducibility
    record M2-T11 stamps onto ``ProfilingRun.sampling`` (populated only when a
    window is enabled); ``plan`` is retained so the CLI can surface
    ``plan.skipped()`` not_computed reasons.
    """

    run_id: str
    mode: ProfileMode
    n_rows: int
    columns: Tuple[ColumnProfile, ...]
    checks: Tuple[QualityCheckResult, ...]
    findings: Tuple[Finding, ...]
    recommendations: Tuple[Recommendation, ...]
    errors: Tuple[RunErrorEntry, ...]
    sampling: Optional[RunSampling]
    plan: ExecutionPlan
    discovery: DiscoveryReport
    handle: RelationHandle
    duplicate_group_counts: Dict[str, int] = field(default_factory=dict)


def open_source(
    dataset_id: str,
    path: "Path | str",
    con: Optional[duckdb.DuckDBPyConnection] = None,
    connector: Optional[str] = None,
    allowed_roots: "Optional[List[Path]]" = None,
) -> Tuple[duckdb.DuckDBPyConnection, RelationHandle]:
    """Open a CSV/Parquet source read-only into a DuckDB connection (MVP
    connectors only, ADR-011) and bind its view. Returns the connection plus
    the :class:`RelationHandle` the pipeline runs against. The caller owns the
    connection lifetime; no HTTP/server (ADR-005) and nothing is loaded into
    Python RAM (ADR-007) — the handle is a lazily-scanned DuckDB view.

    A caller-supplied ``con`` keeps the ceilings it was opened with (a test can
    deliberately pass its own); a connection opened here goes through
    :mod:`data_profiling_engine.duckdb_session` (ADR-021)."""
    con = con if con is not None else duckdb_session.connect()
    impl = get_connector(
        dataset_id, path, connector=connector, allowed_roots=allowed_roots
    )
    return con, impl.register(con)


def _full_row_count(con: duckdb.DuckDBPyConnection, view: str) -> int:
    """Population size of the FULL source relation — the planner gates
    exact/approximate/sampled on the *dataset* size (ENGINE §2.1), while the
    sampled metrics themselves execute on the bounded reservoir the profiler
    builds from ``config.profiling.sampling``."""
    return int(con.execute(f'SELECT count(*) FROM "{view}"').fetchone()[0])


def _sampling_record(sampling) -> Optional[RunSampling]:
    """Translate the §9 ``ConfigSampling`` window into the §2 ``RunSampling``
    reproducibility record (note the key rename ``size`` → ``sample_size``,
    STATE gotcha M1-T3). Returns ``None`` when no window is enabled, so the
    run record stays honest about an unsampled profile."""
    if sampling is None or not sampling.enabled:
        return None
    return RunSampling(
        enabled=True,
        strategy=sampling.strategy,
        sample_size=sampling.size,
        seed=sampling.seed,
    )


def _merge_profiles(
    report: DiscoveryReport, passes: Tuple[ProfilerResult, ...]
) -> Tuple[ColumnProfile, ...]:
    """Merge the family-scoped profiler passes into one profile per column.

    Each pass returns a copy of the discovery-skeleton profile with only its
    own blocks populated; because the blocks are disjoint (see
    :data:`_MERGED_BLOCKS`) the merge is unambiguous. A block a pass left
    ``None`` — a §4 gate-2 section disable, a high-cardinality categorical
    skip, a family scheduled in another task — stays absent, never fabricated
    (ADR-015 honesty carried straight through the composition).
    """
    merged: List[ColumnProfile] = []
    for idx, cd in enumerate(report.columns):
        profile = cd.profile
        update: Dict[str, object] = {}
        for res in passes:
            rp = res.profiles[idx]
            for block in _MERGED_BLOCKS:
                value = getattr(rp, block, None)
                if value is not None and update.get(block) is None:
                    update[block] = value
        # model_copy(update=...) registers the keys in __pydantic_fields_set__
        # so the strict ContractModel emits them (the same mechanism the
        # per-family runners rely on — gotcha M1-T4/M2-T5).
        merged.append(profile.model_copy(update=update) if update else profile)
    return tuple(merged)


#: Stage checkpoint callback (CONTRACTS §5.3 narration + §6.5 cancellation).
#: The pipeline calls it at every stage boundary with a ``<phase>:<task>``
#: label such as ``profiling:distributions_temporal``; the caller may raise to
#: abort the run there. The worker uses exactly that to publish observable
#: progress and to stop a cancelled job between passes instead of waiting out a
#: multi-minute scan (the packaged UI froze at step 1 for the whole compute
#: pass before this existed — M7-T5 run 2).
StageCheckpoint = Callable[[str], None]


def run_profiling_pipeline(
    con: duckdb.DuckDBPyConnection,
    handle: RelationHandle,
    config: DatasetConfig,
    run_id: str,
    registry: Optional[MetricRegistry] = None,
    executed_at: Optional[str] = None,
    checkpoint: Optional[StageCheckpoint] = None,
) -> ProfilingPipelineResult:
    """Run the whole engine path for one already-bound relation, honouring
    ``config.profiling.mode`` + ``.sampling`` + ``.sections`` end-to-end.

    The mode/sampling wiring is *exclusively* through the existing modules:
    :func:`plan_execution` (mode + sampling + sections → per-metric method +
    seed), then the three M2-T4..T6 passes share one plan and one bounded
    reservoir, so a ``sampled`` decision stamps ``method=sampled`` +
    ``method_params.seed`` onto every affected metric (ADR-015). Quality rules
    (§5.1) run against the full relation regardless of mode; findings and
    recommendations interpret/act on the assembled evidence.

    ``checkpoint`` is called *before* each stage runs, so the label always names
    the work in flight; raising from it aborts the pipeline at that boundary.
    """

    def _stage(label: str) -> None:
        if checkpoint is not None:
            checkpoint(label)

    reg = registry if registry is not None else default_registry()
    profiling = config.profiling
    # ``mode`` is a plain str under use_enum_values (STATE gotcha M2-T2);
    # re-hydrate to the enum the planner identity-compares against.
    mode = ProfileMode(profiling.mode)
    sampling = profiling.sampling
    n_rows = _full_row_count(con, handle.view_name)

    _stage("profiling:discover")
    report = discover(con, handle, config=config)
    _stage("profiling:plan")
    plan = plan_execution(
        report,
        mode=mode,
        n_rows=n_rows,
        sampling=sampling,
        sections=profiling.sections,
        registry=reg,
    )

    # The three family-scoped passes run against the SAME report + plan; each
    # starts from the discovery base, so we merge their disjoint blocks below.
    _stage("profiling:statistics_missingness")
    pass_t4 = execute_stat_and_missingness(con, handle, report, plan, config, reg)
    _stage("profiling:uniqueness_categorical")
    pass_t5 = execute_uniqueness_and_categorical(con, handle, report, plan, config, reg)
    _stage("profiling:distributions_temporal")
    pass_t6 = execute_distribution_and_temporal(con, handle, report, plan, config, reg)
    columns = _merge_profiles(report, (pass_t4, pass_t5, pass_t6))

    _stage("quality:rules")
    quality = run_quality_checks(
        con, handle, config, run_id, discovery=report, executed_at=executed_at
    )

    _stage("findings:build")
    findings_result = build_findings(
        dataset_id=handle.dataset_id,
        run_id=run_id,
        config=config,
        profiles=columns,
        checks=quality.checks,
        duplicate_group_counts=pass_t5.duplicate_group_counts,
        row_count=n_rows,
    )
    # build_recommendations returns the findings re-emitted with
    # recommendation_ids wired — those are the persisted findings, not the raw
    # M2-T8 set. ``config.language`` (ADR-018 / BLUEPRINT Q1, default "en")
    # selects the narrative template language only — the shape is unchanged.
    _stage("recommendations:build")
    recs = build_recommendations(findings_result.findings, language=config.language)

    errors: Tuple[RunErrorEntry, ...] = (
        pass_t4.errors + pass_t5.errors + pass_t6.errors + quality.errors
    )

    return ProfilingPipelineResult(
        run_id=run_id,
        mode=mode,
        n_rows=n_rows,
        columns=columns,
        checks=quality.checks,
        findings=recs.findings,
        recommendations=recs.recommendations,
        errors=errors,
        sampling=_sampling_record(sampling),
        plan=plan,
        discovery=report,
        handle=handle,
        duplicate_group_counts=pass_t5.duplicate_group_counts,
    )


def run_dataset_profiling(
    dataset_id: str,
    path: "Path | str",
    config: DatasetConfig,
    run_id: str,
    con: Optional[duckdb.DuckDBPyConnection] = None,
    connector: Optional[str] = None,
    allowed_roots: "Optional[List[Path]]" = None,
    registry: Optional[MetricRegistry] = None,
    executed_at: Optional[str] = None,
    checkpoint: Optional[StageCheckpoint] = None,
) -> ProfilingPipelineResult:
    """Convenience wrapper: open a CSV/Parquet source, then run the pipeline.
    M2-T11's ``profiler-cli`` calls this and folds the result into the §3.1 /
    §2 contract rollups."""
    con, handle = open_source(
        dataset_id, path, con=con, connector=connector, allowed_roots=allowed_roots
    )
    return run_profiling_pipeline(
        con, handle, config, run_id, registry=registry, executed_at=executed_at,
        checkpoint=checkpoint,
    )


__all__ = [
    "ProfilingPipelineResult",
    "StageCheckpoint",
    "open_source",
    "run_profiling_pipeline",
    "run_dataset_profiling",
]
