"""Worker-process entry point for a REAL profiling job (M5-T2; ADR-006).

This replaces the M0-T3 spike ``run_echo_job`` for the production ``profile.start``
path: it runs the whole engine spine (``orchestration.rollup.profile_dataset``,
M2-T11) inside the spawned worker process, writes the M4 reporting artifacts
through the :class:`~data_profiling_engine.storage.artifact_store.ArtifactStore`,
persists the run + its child rows through the
:class:`~data_profiling_engine.storage.metadata_store.MetadataStore`, and reports
bounded progress/terminal events back to the host — never a full result document
over the IPC queue (CONTRACTS §5.4: no large object crosses the bridge as one
payload; large output lives as files referenced by rows, ADR-008).

Spawn-safe by construction (ADR-006): the entry is module-level and every
argument is a picklable primitive (strings / dict / list / a
``multiprocessing`` ``Event`` + ``Queue``). It owns its DuckDB connection
(:func:`profile_dataset` opens one) and never touches the WebView or host-only
state.

Cancellation follows CONTRACTS §6.5: the flag is checked at the step boundaries
this process controls — before the compute begins (so a not-yet-started job
stops cheaply) and after it returns (before any artifact/persist side-effect, so
a cancelled run writes nothing durable). Interrupting the *active* DuckDB query
mid-pipeline is a Phase-3 depth item, not MVP (§6.5 "for DuckDB interrupts the
active query"); the heavy pass runs as one bounded call, so a cancel issued while
it executes is observed at the next boundary — a real limitation, recorded in
STATE, never silently glossed over.

Failures map to the CONTRACTS §8 registry: an unreadable/forbidden source
(:class:`ConnectorError`), an unwritable artifact (:class:`ArtifactStoreError`)
or a metadata write failure (:class:`MetadataStoreError`) carry their own
``code``; an invalid config is ``CONFIG_INVALID`` (rejected here defensively even
though the facade validates first, AGENTS §5); any other exception is a systemic
worker failure surfaced as ``WORKER_CRASHED`` — the job FAILED, the host survives.
"""
from __future__ import annotations

import copy
from multiprocessing.synchronize import Event as BaseEvent
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any, Dict, List, Optional

from .events import ProgressEvent, TerminalEvent
from .models import JobState

#: Number of observable step boundaries a profiling job reports (§5.5 poll —
#: coarse, because the engine spine is one bounded compute call).
PROFILING_TOTAL_STEPS = 6

# Step labels use the ``<phase>:<task>`` convention of the CONTRACTS §5.3 status
# sample (``profiling:distributions``); they are progress narration, not a
# contract enum.
_STEP_VALIDATE = "profiling:validate_config"
_STEP_METRICS = "profiling:metrics"
_STEP_JSON = "reporting:json"
_STEP_HTML = "reporting:html"
_STEP_CSV = "reporting:csv"
_STEP_PERSIST = "persist:metadata"


class ProfileJobFailed(Exception):
    """Internal control-flow error carrying a §8 registry code string."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _load_config(config_document: Dict[str, Any], profile_mode: Optional[str]):
    """Validate the §9 config document (schema, then model) and apply the
    optional ``profile_mode`` override, mirroring ``cli.load_config_from_dict``.

    An invalid document raises :class:`ProfileJobFailed` with ``CONFIG_INVALID``
    so nothing computes against a rejected config (AGENTS §5). Imported lazily so
    a spawned child resolves these in its own process."""
    from data_profiling_engine import schemas as S
    from data_profiling_engine.schemas.models import DatasetConfig

    document = config_document
    if profile_mode is not None:
        document = copy.deepcopy(config_document)
        document.setdefault("profiling", {})["mode"] = profile_mode

    errors = S.validate("dataset_config.schema.json", document)
    if errors:
        raise ProfileJobFailed("CONFIG_INVALID", "config failed schema validation")
    try:
        return DatasetConfig.model_validate(document)
    except Exception as exc:  # pydantic ValidationError — never echo the input
        raise ProfileJobFailed("CONFIG_INVALID", "config failed model validation") from exc


def _error_code(exc: BaseException) -> str:
    """Map a caught exception to its CONTRACTS §8 registry code string."""
    from data_profiling_engine.connectors.base import ConnectorError
    from data_profiling_engine.storage.artifact_store import ArtifactStoreError
    from data_profiling_engine.storage.metadata_store import MetadataStoreError

    if isinstance(exc, ProfileJobFailed):
        return exc.code
    if isinstance(exc, (ConnectorError, ArtifactStoreError, MetadataStoreError)):
        return exc.code.value
    return "WORKER_CRASHED"


def run_profile_job(
    job_id: str,
    dataset_id: str,
    source_path: str,
    config_document: Dict[str, Any],
    run_id: str,
    profile_mode: Optional[str],
    allowed_roots: Optional[List[str]],
    data_root: Optional[str],
    cancel_event: BaseEvent,
    event_queue: Queue,
) -> None:
    """Execute one end-to-end profiling job in the worker process.

    Writes the canonical ``profile_json`` / ``html_report`` / ``issue_csv``
    artifacts and the metadata rows, then emits a SUCCEEDED terminal whose
    ``result`` is the *bounded* run summary (ids + store-relative artifact
    refs + counts) — never the result document itself (§5.4)."""
    from data_profiling_engine.orchestration.rollup import profile_dataset
    from data_profiling_engine.reporting import result_document
    from data_profiling_engine.reporting.csv_export import emit_issue_csv_artifact
    from data_profiling_engine.reporting.html_report import emit_html_report_artifact
    from data_profiling_engine.storage.artifact_store import ArtifactStore
    from data_profiling_engine.storage.metadata_store import MetadataStore

    def _progress(completed: int, current_step: str) -> None:
        event_queue.put(
            ProgressEvent(
                job_id=job_id,
                completed_steps=completed,
                total_steps=PROFILING_TOTAL_STEPS,
                current_step=current_step,
            )
        )

    def _cancelled() -> bool:
        if cancel_event.is_set():
            event_queue.put(TerminalEvent(job_id=job_id, status=JobState.CANCELLED))
            return True
        return False

    try:
        # 1. Config (defensive re-validation; the facade validated it first).
        config = _load_config(config_document, profile_mode)
        _progress(1, _STEP_VALIDATE)
        if _cancelled():
            return

        # The host allow-list arrives as strings; a job with no roots keeps the
        # connector's own default policy (None), exactly like the CLI.
        roots = [Path(r) for r in allowed_roots] if allowed_roots else None
        store = ArtifactStore(data_root=data_root)
        # Precompute the store-relative refs so ``profile_dataset`` can stamp
        # them onto ``run.result_refs`` before the files exist; the emit helpers
        # then write to those exact canonical names (DEFAULT_FILENAMES).
        refs = {
            kind: store.ref_path(run_id, kind)
            for kind in ("profile_json", "html_report", "issue_csv")
        }

        # 2. The engine spine — open source (read-only, path-allow-listed) +
        #    discover/plan/profile/quality/findings/recommendations + rollup.
        bundle = profile_dataset(
            dataset_id, source_path, config, run_id,
            allowed_roots=roots, result_refs=refs, job_id=job_id,
        )
        _progress(2, _STEP_METRICS)
        if _cancelled():
            return

        # 3-5. Write the M4 reporting artifacts through the store.
        store.write_document(run_id, "profile_json", result_document(bundle))
        _progress(3, _STEP_JSON)
        emit_html_report_artifact(bundle, store)
        _progress(4, _STEP_HTML)
        emit_issue_csv_artifact(bundle, store)
        _progress(5, _STEP_CSV)
        if _cancelled():
            return

        # 6. Persist the run + child rows in one atomic transaction (the host
        #    already created the ``jobs`` row, so ``runs.job_id`` resolves).
        metadata = MetadataStore(data_root=data_root)
        metadata.save_run(bundle, config=config)

        event_queue.put(
            TerminalEvent(
                job_id=job_id,
                status=JobState.SUCCEEDED,
                result=_bounded_result(bundle, refs),
            )
        )
    except Exception as exc:  # noqa: BLE001 — boundary: never leak a traceback
        event_queue.put(
            TerminalEvent(
                job_id=job_id,
                status=JobState.FAILED,
                error_code=_error_code(exc),
            )
        )


def _bounded_result(bundle: Any, refs: Dict[str, str]) -> Dict[str, Any]:
    """The small, §5.4-bounded status payload a SUCCEEDED job returns: ids, the
    store-relative artifact refs (so ``artifact.open`` can reach them) and a few
    counts for the report view. The full document stays on disk, never here."""
    profile = bundle.profile
    row_count = None
    if getattr(profile, "row_count", None) is not None:
        row_count = profile.row_count.value
    return {
        "run_id": bundle.run.run_id,
        "dataset_id": bundle.run.dataset.dataset_id,
        "result_refs": dict(refs),
        "row_count": row_count,
        "column_count": profile.column_count,
        "finding_count": len(bundle.findings),
        "check_count": len(bundle.checks),
        "recommendation_count": len(bundle.recommendations),
        "error_count": len(bundle.errors),
    }


__all__ = [
    "PROFILING_TOTAL_STEPS",
    "ProfileJobFailed",
    "run_profile_job",
]
