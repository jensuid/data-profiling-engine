"""Contract rollup assembly (M2-T11; CONTRACTS §2–3, ENGINE §10).

The engine spine (:func:`orchestration.run_dataset_profiling`, M2-T10) returns a
:class:`~data_profiling_engine.orchestration.ProfilingPipelineResult` — the
assembled column profiles, quality checks, findings, recommendations, the union
of per-task errors, the §2 sampling record and the plan — but deliberately
stops short of the two dataset-level contract objects, because no runner ever
executed the *dataset-scope* plan decisions:

    ``schema.row_count`` / ``schema.column_count`` / ``schema.key_candidates``

This module closes that gap. It executes those dataset decisions against the
bound DuckDB relation (honouring each decision's ``method`` label and the
registry's ``requires_full_relation`` flag), then folds the pipeline result
plus those scalars into the frozen §3.1 :class:`DatasetProfile` and §2
:class:`ProfilingRun` records. **No new metric is registered and no computation
is invented here** — the values come from the same registry templates / SQL the
column profilers use, and every number keeps the ``Provenance`` its planner
decision stamped (ADR-015).

Honesty carried through the rollup:
  - ``row_count`` / ``column_count`` are cheap_exact metrics that stay ``exact``
    even under a sampling window (ENGINE §2.1 "exact always" — we recompute on
    the full relation, we never relabel a sample).
  - ``key_candidates`` declares ``requires_full_relation`` (a primary key is
    only a key over the WHOLE relation), so it is verified against the full
    view; an un-plannable / not_computed decision yields an ABSENT candidate
    list, never a fabricated one (AGENTS §3).
  - the run status is the §6.2 terminal ``SUCCEEDED`` whenever the pipeline
    produced a result; per-task failures ride in ``error_log`` without
    demoting the run (§6.4 FAIL≠ERROR policy applied at run level).
"""
from __future__ import annotations

import hashlib
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import duckdb

from data_profiling_engine import duckdb_session
from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.engine.metrics import MetricRegistry, default_registry
from data_profiling_engine.engine.planner import (
    ExecutionPlan,
    PlanDecision,
    render_sql,
)
from data_profiling_engine.orchestration import ProfilingPipelineResult
from data_profiling_engine.schemas.models import (
    DatasetConfig,
    DatasetProfile,
    MetricMethod,
    MetricValue,
    ProfilingRun,
    Provenance,
    RunConfig,
    RunDataset,
    RunEngine,
    RunErrorEntry,
    RunStatus,
    RunTiming,
    SourceKind,
    TemporalCoverage,
)

#: Contract schema version emitted on every persisted object (CONTRACTS §1).
SCHEMA_VERSION = "1.0"

#: Engine build version stamped on ``ProfilingRun.engine`` (CONTRACTS §2
#: example ``0.1.0``; the reproducibility tuple content_hash + config_hash +
#: engine_version + seed). Bump alongside the packaging version.
ENGINE_VERSION = "0.1.0"


def _rfc3339_z(dt: datetime) -> str:
    """Timezone-aware datetime → ISO-8601 UTC ``...Z`` (CONTRACTS §1 timestamps)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _instantiate(cls, **kwargs):
    """Build a strict contract model, honouring the ``ContractModel`` null/absence
    policy: an optional field that the canonical schema does NOT type as nullable
    must be OMITTED rather than passed as ``None`` (the before-validator rejects
    an explicit null on a non-nullable key — M1-T4 gotcha). Fields in the model's
    ``NULLABLE_FIELDS`` keep an explicit ``None`` (dumped as a valid JSON null)."""
    nullable = cls.NULLABLE_FIELDS
    data = {k: v for k, v in kwargs.items() if v is not None or k in nullable}
    return cls.model_validate(data)


def config_hash(config: DatasetConfig) -> str:
    """Deterministic ``sha256:`` digest of a dataset config (CONTRACTS §2
    ``config.config_hash``). ``DatasetConfig.model_dump_json`` walks fields in
    declaration order, so the same logical config always hashes identically —
    the reproducibility anchor for a run."""
    digest = hashlib.sha256(config.model_dump_json().encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True)
class DatasetMetrics:
    """Dataset-scope rollup scalars, each carrying (or honestly lacking) the
    provenance its planner decision stamped."""

    row_count: Optional[MetricValue] = None
    column_count: Optional[int] = None
    primary_key_candidates: Optional[List[str]] = None
    errors: Tuple[RunErrorEntry, ...] = field(default_factory=tuple)


def _decision_of(plan: ExecutionPlan, metric_id: str) -> Optional[PlanDecision]:
    for d in plan.dataset_decisions:
        if d.metric_id == metric_id:
            return d
    return None


def _execute_scalar_decision(
    con: duckdb.DuckDBPyConnection,
    handle: RelationHandle,
    decision: Optional[PlanDecision],
    registry: MetricRegistry,
    *,
    full_relation: bool,
) -> Optional[MetricValue]:
    """Render + run one templated dataset decision into a contract
    :class:`MetricValue`. A ``not_computed`` (or absent) decision yields an
    honest ``not_computed`` value with the reason and NO ``value`` key
    (AGENTS §3 — never a silent null).

    ``full_relation`` forces the FULL view regardless of the sampled label —
    used for cheap-exact-under-sampling / requires-full-relation metrics so a
    population count is never quietly relabelled as a sample (ENGINE §2.1)."""
    if decision is None:
        return None
    if decision.method is MetricMethod.NOT_COMPUTED:
        mv = MetricValue.model_construct(
            value=None,
            provenance=Provenance(
                method=MetricMethod.NOT_COMPUTED, reason=decision.reason or "not computed"
            ),
        )
        mv.model_fields_set.discard("value")
        return mv
    spec = registry.get(decision.metric_id)
    on_full = full_relation or decision.method is not MetricMethod.SAMPLED
    view = handle.view_name if on_full else _sample_view_for(con, handle, decision)
    try:
        sql = render_sql(spec, view)
        value = con.execute(sql).fetchone()[0]
    except duckdb.Error:
        # A dataset-scope aggregate that fails to execute is recorded upstream as
        # an error entry; here we degrade honestly to not_computed.
        mv = MetricValue.model_construct(
            value=None,
            provenance=Provenance(
                method=MetricMethod.NOT_COMPUTED, reason="dataset metric execution failed"
            ),
        )
        mv.model_fields_set.discard("value")
        return mv
    return MetricValue(value=int(value or 0), provenance=Provenance(**decision.provenance_kwargs()))


def _sample_view_for(
    con: duckdb.DuckDBPyConnection, handle: RelationHandle, decision: PlanDecision
) -> str:
    """Rebuild (content-hashed, idempotent) the one bounded reservoir the column
    profilers used, so a genuinely-sampled dataset metric reads the SAME subset.
    Only reached for a non-cheap metric whose decision is ``sampled``."""
    # Imported lazily to avoid an import cycle with the profiler module.
    from data_profiling_engine.engine.metrics.profiler import _sample_relation

    params = decision.params
    return _sample_relation(
        con,
        handle.view_name,
        str(params.get("strategy") or "reservoir"),
        int(params.get("sample_size") or 0),
        int(params.get("seed") or 0),
    )


def _key_candidates(
    con: duckdb.DuckDBPyConnection,
    handle: RelationHandle,
    decision: Optional[PlanDecision],
) -> Optional[List[str]]:
    """Columns that uniquely identify rows over the FULL relation (distinct ==
    non-null and non-null > 0). A key is only a key across the whole dataset, so
    ``requires_full_relation`` pins this to the full view; an absent or
    not_computed decision returns ``None`` so the field is omitted, never a
    fabricated empty claim."""
    if decision is None or decision.method is MetricMethod.NOT_COMPUTED:
        return None
    view = handle.view_name  # requires_full_relation=True on schema.key_candidates
    candidates: List[str] = []
    for name in handle.column_names:
        col = '"' + name.replace('"', '""') + '"'
        try:
            distinct, non_null = con.execute(
                f"SELECT count(DISTINCT {col}), count({col}) FROM \"{view}\""
            ).fetchone()
        except duckdb.Error:
            continue  # per-column failure keeps the others; that column is not a candidate
        if non_null and non_null > 0 and int(distinct) == int(non_null):
            candidates.append(name)
    return candidates


def execute_dataset_metrics(
    con: duckdb.DuckDBPyConnection,
    handle: RelationHandle,
    plan: ExecutionPlan,
    registry: Optional[MetricRegistry] = None,
) -> DatasetMetrics:
    """Run the three dataset-scope decisions the column profilers never touch.

    ``schema.physical_types`` / ``schema.logical_semantic_types`` are also
    dataset-scope registry rows, but their ``contract_target`` lands on
    ``column_profile`` (already populated by discovery), so they carry no
    ``DatasetProfile`` field and are intentionally skipped here."""
    reg = registry if registry is not None else default_registry()

    row_count = _execute_scalar_decision(
        con, handle, _decision_of(plan, "schema.row_count"), reg, full_relation=True
    )

    # column_count is a plain int on the contract (no provenance carrier); the
    # honest population is the source column set the connector bound.
    cc_decision = _decision_of(plan, "schema.column_count")
    column_count: Optional[int] = None
    if cc_decision is not None and cc_decision.method is not MetricMethod.NOT_COMPUTED:
        column_count = len(handle.column_names)

    key_candidates = _key_candidates(con, handle, _decision_of(plan, "schema.key_candidates"))

    return DatasetMetrics(
        row_count=row_count,
        column_count=column_count,
        primary_key_candidates=key_candidates,
    )


def _temporal_coverage(result: ProfilingPipelineResult) -> Optional[TemporalCoverage]:
    """Dataset time span from the first discovered temporal column (ordinal
    order). Range endpoints are cheap_exact / exact-under-sampling, so the
    coverage is honestly labelled ``exact`` — it is the observed min/max of the
    full relation the temporal pass already computed (CONTRACTS §3.1)."""
    temporal_logical = {"temporal_date", "temporal_time", "temporal_datetime"}
    for cd in result.discovery.columns:
        if cd.logical.value not in temporal_logical:
            continue
        profile = next((c for c in result.columns if c.column == cd.profile.column), None)
        if profile is None or profile.temporal is None or profile.temporal.range is None:
            continue
        rng = profile.temporal.range
        if rng.min is None and rng.max is None:
            continue
        return TemporalCoverage(
            column=profile.column,
            min=rng.min,
            max=rng.max,
            provenance=Provenance(method=MetricMethod.EXACT),
        )
    return None


def build_dataset_profile(
    con: duckdb.DuckDBPyConnection,
    result: ProfilingPipelineResult,
    config: DatasetConfig,
    registry: Optional[MetricRegistry] = None,
) -> DatasetProfile:
    """Assemble the §3.1 ``DatasetProfile`` from the pipeline result + the
    dataset-scope decisions it never executed."""
    metrics = execute_dataset_metrics(
        con, result.handle, result.plan, registry=registry
    )
    source_meta = dict(result.handle.extra_metadata) or None
    return _instantiate(
        DatasetProfile,
        schema_version=SCHEMA_VERSION,
        run_id=result.run_id,
        row_count=metrics.row_count,
        column_count=metrics.column_count,
        grain=config.dataset.grain,
        primary_key_candidates=metrics.primary_key_candidates,
        temporal_coverage=_temporal_coverage(result),
        source_metadata=source_meta,
        columns=list(result.columns),
    )


def build_profiling_run(
    result: ProfilingPipelineResult,
    config: DatasetConfig,
    started_at: datetime,
    finished_at: datetime,
    *,
    status: RunStatus = RunStatus.SUCCEEDED,
    result_refs: Optional[Dict[str, str]] = None,
    job_id: Optional[str] = None,
) -> ProfilingRun:
    """Assemble the §2 ``ProfilingRun`` record. ``dataset`` / ``source_snapshot``
    come verbatim from the bound handle; ``config_hash`` + ``sampling`` +
    terminal ``status`` + ``error_log`` complete the reproducibility tuple
    (content_hash + config_hash + engine_version + seed)."""
    duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    error_log = list(result.errors) or None
    return _instantiate(
        ProfilingRun,
        schema_version=SCHEMA_VERSION,
        run_id=result.run_id,
        job_id=job_id,
        dataset=RunDataset(
            dataset_id=result.handle.dataset_id,
            source_kind=SourceKind.FILE,
            connector=result.handle.connector,
            source_ref=result.handle.snapshot.path or result.handle.dataset_id,
            source_snapshot=result.handle.snapshot,
        ),
        config=RunConfig(
            config_version=str(config.config_version),
            config_hash=config_hash(config),
            profile_mode=result.mode,
        ),
        engine=RunEngine(
            engine_version=ENGINE_VERSION,
            duckdb_version=duckdb.__version__,
            python_version=platform.python_version(),
        ),
        timing=RunTiming(
            started_at=_rfc3339_z(started_at),
            finished_at=_rfc3339_z(finished_at),
            duration_ms=duration_ms,
        ),
        sampling=result.sampling,
        status=status,
        result_refs=result_refs,
        error_log=error_log,
        warnings=[],
    )


@dataclass(frozen=True)
class ProfileBundle:
    """The complete, contract-valid output of one end-to-end profiling run —
    the ``ProfilingRun`` + ``DatasetProfile`` + every per-column / per-check /
    finding / recommendation object the reporting layer serialises (ENGINE §10)."""

    run: ProfilingRun
    profile: DatasetProfile
    checks: Tuple[object, ...]
    findings: Tuple[object, ...]
    recommendations: Tuple[object, ...]
    errors: Tuple[RunErrorEntry, ...]


def profile_dataset(
    dataset_id: str,
    path,
    config: DatasetConfig,
    run_id: str,
    *,
    con: Optional[duckdb.DuckDBPyConnection] = None,
    connector: Optional[str] = None,
    allowed_roots=None,
    registry: Optional[MetricRegistry] = None,
    result_refs: Optional[Dict[str, str]] = None,
    job_id: Optional[str] = None,
) -> ProfileBundle:
    """Open a CSV/Parquet source, run the full engine path (M2-T10), then fold
    the result + dataset-scope metrics into the §3.1 / §2 contract rollups. This
    is the single entry the ``profiler-cli`` (and the M5 worker) drives; heavy
    compute stays inside DuckDB / the caller's connection, never the UI thread
    (ADR-006/007) and no HTTP server is involved (ADR-005).

    ``result_refs`` is the artifact-path map the caller knows up front (e.g.
    the CLI's ``--out`` file) so the run record's reproducibility tuple can
    point at the written document. ``job_id`` links the run to the §6 job that
    produced it — set by the M5 bridge job manager (``profile.start``) so
    ``MetadataStore.save_run`` can resolve the ``runs.job_id`` foreign key; the
    headless CLI leaves it ``None`` (absent on the §2 record, per contract).
    """
    from data_profiling_engine.orchestration import run_dataset_profiling

    con = con if con is not None else duckdb_session.connect()
    started_at = datetime.now(timezone.utc)
    result = run_dataset_profiling(
        dataset_id, path, config, run_id,
        con=con, connector=connector, allowed_roots=allowed_roots, registry=registry,
    )
    finished_at = datetime.now(timezone.utc)
    profile = build_dataset_profile(con, result, config, registry=registry)
    run = build_profiling_run(
        result, config, started_at, finished_at,
        result_refs=result_refs, job_id=job_id,
    )
    return ProfileBundle(
        run=run,
        profile=profile,
        checks=result.checks,
        findings=result.findings,
        recommendations=result.recommendations,
        errors=result.errors,
    )


__all__ = [
    "SCHEMA_VERSION",
    "ENGINE_VERSION",
    "config_hash",
    "DatasetMetrics",
    "execute_dataset_metrics",
    "build_dataset_profile",
    "build_profiling_run",
    "ProfileBundle",
    "profile_dataset",
]
