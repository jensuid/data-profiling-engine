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

Cancellation follows CONTRACTS §6.5 on both of its halves. Between stages the
engine spine calls back into this module at every boundary (stage checkpoints),
so a cancel stops the run before the next pass starts and before any
artifact/persist side-effect (a cancelled run writes nothing durable). During a
long pass a watchdog thread calls ``DuckDBPyConnection.interrupt()`` on the
connection this process owns, which aborts the *active* query in ~20 ms and
surfaces as ``duckdb.InterruptException`` in the compute thread — mapped to
``CANCELLED``, never to ``WORKER_CRASHED``. Both halves are needed: a checkpoint
alone waits out the running pass (the packaged build showed a 4 min 18 s cancel
latency on a 2 M-row source, M7-T5 run 2), an interrupt alone cannot stop a job
that is between queries.

Failures map to the CONTRACTS §8 registry: an unreadable/forbidden source
(:class:`ConnectorError`), an unwritable artifact (:class:`ArtifactStoreError`)
or a metadata write failure (:class:`MetadataStoreError`) carry their own
``code``; an invalid config is ``CONFIG_INVALID`` (rejected here defensively even
though the facade validates first, AGENTS §5); any other exception is a systemic
worker failure surfaced as ``WORKER_CRASHED`` — the job FAILED, the host survives.
"""
from __future__ import annotations

import copy
import threading
from multiprocessing.synchronize import Event as BaseEvent
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any, Dict, List, Optional

from .events import ProgressEvent, TerminalEvent
from .models import JobState

#: Number of observable step boundaries a profiling job reports (§5.5 poll).
#: These are the engine's stage checkpoints plus the worker-owned
#: reporting/persist steps, so the bar advances at every real boundary. It used
#: to hold a single ``profiling:metrics`` entry for the whole compute pass,
#: which on a 2 M-row source left the packaged UI frozen at ``1/6 · 17 %`` for
#: ~4 minutes (M7-T5 run 2 — "job observable", TESTING §5.1).
# Step labels use the ``<phase>:<task>`` convention of the CONTRACTS §5.3 status
# sample (``profiling:distributions``); they are progress narration, not a
# contract enum.
_STEP_VALIDATE = "profiling:validate_config"
_STEP_DISCOVER = "profiling:discover"
_STEP_PLAN = "profiling:plan"
_STEP_STATS = "profiling:statistics_missingness"
_STEP_UNIQUENESS = "profiling:uniqueness_categorical"
_STEP_DISTRIBUTION = "profiling:distributions_temporal"
_STEP_QUALITY = "quality:rules"
_STEP_FINDINGS = "findings:build"
_STEP_RECOMMENDATIONS = "recommendations:build"
_STEP_DATASET_METRICS = "profiling:dataset_metrics"
_STEP_JSON = "reporting:json"
_STEP_HTML = "reporting:html"
_STEP_CSV = "reporting:csv"
_STEP_PERSIST = "persist:metadata"

#: Ordered ladder every emitted label must come from; the engine emits the
#: middle nine (``orchestration.run_profiling_pipeline`` /
#: ``rollup.profile_dataset`` call the checkpoint with exactly these strings).
_STAGE_LADDER = (
    _STEP_VALIDATE,
    _STEP_DISCOVER,
    _STEP_PLAN,
    _STEP_STATS,
    _STEP_UNIQUENESS,
    _STEP_DISTRIBUTION,
    _STEP_QUALITY,
    _STEP_FINDINGS,
    _STEP_RECOMMENDATIONS,
    _STEP_DATASET_METRICS,
    _STEP_JSON,
    _STEP_HTML,
    _STEP_CSV,
    _STEP_PERSIST,
)

PROFILING_TOTAL_STEPS = len(_STAGE_LADDER)
_STEP_INDEX = {label: i + 1 for i, label in enumerate(_STAGE_LADDER)}

#: How often the cancel watchdog re-checks the flag. The interrupt itself is
#: immediate; this only bounds how long after ``profile.cancel`` it fires.
_CANCEL_POLL_SECONDS = 0.2


class _JobCancelled(Exception):
    """Internal control flow: a checkpoint saw a pending cancel and stopped."""


class ProfileJobFailed(Exception):
    """Internal control-flow error carrying a §8 registry code string."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _start_interrupt_watch(
    con: Any, cancel_event: BaseEvent, stop_event: threading.Event
) -> threading.Thread:
    """Keep aborting the worker's DuckDB query for as long as a cancel is pending.

    CONTRACTS §6.5 asks the worker to interrupt the running query, not merely to
    notice the flag at the next boundary, and the packaged build showed why: a
    cancel issued during the 2 M-row pass waited 4 min 18 s for that pass to
    finish. ``DuckDBPyConnection.interrupt()`` aborts the running query from
    another thread (~20 ms). Between queries the call is harmless, which is why
    the stage checkpoints remain the precise mechanism there.

    The interrupt is RE-ARMED rather than fired once: ``interrupt()`` cancels the
    query running at that instant, and the metric loop treats an interrupted query
    as one ``METRIC_NOT_COMPUTED`` row and carries on to the next one
    (``engine/metrics/profiler.py`` catches ``duckdb.Error`` per metric). A single
    shot therefore stops exactly one metric — measured on the 2 M-row fixture: the
    pass still ran 114 s to completion before the next checkpoint noticed. While
    the cancel stands, every following query aborts in milliseconds, so the pass
    unwinds instead of finishing.
    """

    def watch() -> None:
        while not stop_event.is_set():
            if not cancel_event.wait(_CANCEL_POLL_SECONDS):
                continue
            try:
                con.interrupt()
            except Exception:  # noqa: BLE001 — best effort: the checkpoints still stop the job
                return

    thread = threading.Thread(target=watch, name="profile-cancel-watch", daemon=True)
    thread.start()
    return thread


def _interrupted_by_cancel(exc: BaseException, cancel_event: BaseEvent) -> bool:
    """True when an exception is DuckDB's answer to our own cancel request.

    Both conditions are required, so an unrelated DuckDB failure that happens to
    race a cancel is still reported as FAILED rather than silently excused.
    Imported lazily: the worker module stays cheap for the spawn parent.
    """
    if not cancel_event.is_set():
        return False
    import duckdb

    return isinstance(exc, duckdb.InterruptException)


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

    position = {"step": 0}

    def _advance(label: str) -> None:
        """Publish one stage boundary, then stop if a cancel is pending.

        This is both the engine checkpoint (``profile_dataset`` calls it before
        every pass) and the local emitter for the steps this function owns, so
        one monotonic ladder feeds the UI. An unknown label keeps the previous
        step: progress can never overshoot ``total_steps`` if the engine grows a
        stage the ladder has not learned yet.
        """
        step = _STEP_INDEX.get(label, position["step"])
        position["step"] = step
        _progress(step, label)
        if cancel_event.is_set():
            raise _JobCancelled()

    con = None
    watch_stop = threading.Event()
    try:
        # 1. Config (defensive re-validation; the facade validated it first).
        config = _load_config(config_document, profile_mode)
        _advance(_STEP_VALIDATE)

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

        # 2-10. The engine spine — open source (read-only, path-allow-listed) +
        #    discover/plan/profile/quality/findings/recommendations + rollup.
        #    The connection is opened HERE so the cancel watchdog can interrupt
        #    the active query (§6.5); the engine applies the same ADR-021
        #    ceilings because it is the same ``duckdb_session.connect``.
        from data_profiling_engine import duckdb_session

        con = duckdb_session.connect()
        _start_interrupt_watch(con, cancel_event, watch_stop)
        bundle = profile_dataset(
            dataset_id, source_path, config, run_id,
            con=con, allowed_roots=roots, result_refs=refs, job_id=job_id,
            checkpoint=_advance,
        )

        # 11-13. Write the M4 reporting artifacts through the store.
        _advance(_STEP_JSON)
        store.write_document(run_id, "profile_json", result_document(bundle))
        _advance(_STEP_HTML)
        emit_html_report_artifact(bundle, store)
        _advance(_STEP_CSV)
        emit_issue_csv_artifact(bundle, store)

        # 14. Persist the run + child rows in one atomic transaction (the host
        #    already created the ``jobs`` row, so ``runs.job_id`` resolves).
        #    Reported AFTER the write, so a job sitting at ``total_steps`` always
        #    has durable metadata behind it. Without it the last progress event
        #    stayed at the previous step and a SUCCEEDED job advertised less than
        #    100 % forever (caught on the packaged build, M7-T5 run 2).
        metadata = MetadataStore(data_root=data_root)
        metadata.save_run(bundle, config=config)
        _progress(_STEP_INDEX[_STEP_PERSIST], _STEP_PERSIST)

        event_queue.put(
            TerminalEvent(
                job_id=job_id,
                status=JobState.SUCCEEDED,
                result=_bounded_result(bundle, refs),
            )
        )
    except _JobCancelled:
        # The checkpoint already published the boundary that stopped us; the
        # cancel itself is confirmed here, so the bar and the state agree.
        event_queue.put(TerminalEvent(job_id=job_id, status=JobState.CANCELLED))
    except Exception as exc:  # noqa: BLE001 — boundary: never leak a traceback
        if _interrupted_by_cancel(exc, cancel_event):
            # §6.5: a cancel that landed mid-query is a CANCELLED job, not a
            # worker failure. Nothing durable was written (persist is last).
            event_queue.put(TerminalEvent(job_id=job_id, status=JobState.CANCELLED))
        else:
            event_queue.put(
                TerminalEvent(
                    job_id=job_id,
                    status=JobState.FAILED,
                    error_code=_error_code(exc),
                )
            )
    finally:
        watch_stop.set()
        if con is not None:
            con.close()


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
