"""Host-side job manager for the M0-T3 spike (CONTRACTS §6.3 minimum set).

Owns one spawned worker process per job plus a **thread-safe status slot**
the bridge facade can poll from any thread. Deliberately spike-scoped:
in-memory only (persistence/recovery are M3-T1/M5-T2), but the lifecycle
surface already matches §6.3 — start, status, progress reporting, cancel,
timeout, error reporting, worker-crash detection.

Feeder-thread invariant: worker events are buffered and applied *in order*
per job; we only declare the process dead once it has exited AND the buffer
has been dry for a full poll cycle — so a terminal event racing process
teardown is never misread as a crash (SUCCEEDED wins over FAILED).

§5.5 push observers (M5-T3): every transition a job goes through is also handed
to registered listeners as the canonical status dict, on a dedicated dispatcher
thread — never under the manager lock and never on the feeder thread, so a slow
or dead WebView cannot stall a job. Polling stays authoritative; push is a hint.
"""
from __future__ import annotations

import multiprocessing
import queue as queue_module
import threading
import time
import uuid
from dataclasses import dataclass
from multiprocessing.context import SpawnContext
from typing import Any, Callable

from .. import errors
from .events import ProgressEvent, TerminalEvent
from .models import JobProgress, JobState, JobStatus
from .process import run_echo_job
from .profile_job import run_profile_job

# Spike defaults; per-mode timeouts are wired in M2-T10/M5-T2.
DEFAULT_JOB_TIMEOUT_SECONDS = 300.0
# A real profiling job runs the whole engine spine + writes artifacts; the
# default ceiling is generous (Phase-3 benchmarks tune a per-mode value).
PROFILE_JOB_TIMEOUT_SECONDS = 1800.0
_FORCE_KILL_GRACE_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 0.05

#: What a §5.5 observer receives: one canonical §5.3 status object, by value.
StatusListener = Callable[["dict[str, Any]"], None]


@dataclass
class _JobRecord:
    """Mutable per-job state; every access is guarded by the manager lock.

    ``persist`` marks a real profiling job whose transitions mirror into the
    SQLite metadata store (§6.3 persistence of critical state); the echo spike
    leaves it ``False`` so the in-memory path is untouched."""

    status: JobStatus
    process: Any = None  # SpawnContext.Process | None
    cancel_event: Any = None
    queue: Any = None
    deadline: float = float("inf")
    cancel_requested: bool = False
    terminal_seen: bool = False
    running_marked: bool = False
    persist: bool = False
    thread: threading.Thread | None = None


class JobManager:
    """Registry of in-flight spike jobs (one worker process each)."""

    def __init__(
        self,
        timeout_seconds: float = DEFAULT_JOB_TIMEOUT_SECONDS,
        metadata_store: Any = None,
    ) -> None:
        self._ctx: SpawnContext = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._jobs: dict[str, _JobRecord] = {}
        self._timeout_seconds = timeout_seconds
        # Optional SQLite mirror for real job state (§6.3). The spike manager
        # runs with it ``None`` (pure in-memory status slot).
        self._store = metadata_store
        # §5.5 push observers. Dispatch is deferred to one lazy daemon thread so
        # a listener (a WebView round-trip) never runs while the manager lock is
        # held and never blocks the feeder thread that folds worker events.
        self._listeners: list[StatusListener] = []
        self._pending: dict[str, dict[str, Any]] = {}
        self._wake = threading.Event()
        self._dispatcher: threading.Thread | None = None

    def add_status_listener(self, listener: StatusListener) -> None:
        """Subscribe to status pushes for every job this manager owns (§5.5).

        Idempotent per callable. The dispatcher thread starts on the first
        listener, so an app that only polls pays nothing. The payload is exactly
        what ``profile.get_status`` returns (see :func:`canonical_status_payload`)
        — one schema, so the UI cannot care which transport delivered it."""
        with self._lock:
            if any(existing is listener for existing in self._listeners):
                return
            self._listeners.append(listener)
            if self._dispatcher is None:
                self._dispatcher = threading.Thread(
                    target=self._dispatch_loop, daemon=True, name="job-status-push"
                )
                self._dispatcher.start()

    def _notify(self, record: _JobRecord) -> None:
        """Queue the record's current status for observers. Must be called while
        ``self._lock`` is held (right after each transition is mirrored), so it
        only takes an in-memory snapshot — no listener is invoked here.

        Coalesced by job id: the newest status wins, so a burst of progress
        events collapses into one push and a terminal transition can never be
        dropped behind a stale one. A consumer that missed an intermediate step
        still sees the truth by polling."""
        if not self._listeners:
            return
        self._pending[record.status.job_id] = canonical_status_payload(record.status)
        self._wake.set()

    def _dispatch_loop(self) -> None:
        """Deliver queued statuses outside the lock; listener faults are theirs.

        A push is a hint (§5.5): an exception from the host sink (closed window,
        stalled WebView) is swallowed rather than surfacing as a job failure —
        the job's own state and the SQLite mirror are unaffected."""
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                batch = list(self._pending.values())
                self._pending.clear()
                listeners = list(self._listeners)
            for payload in batch:
                for listener in listeners:
                    try:
                        listener(payload)
                    except Exception:  # noqa: BLE001 — push never fails a job
                        pass

    # ---- public API (called from facade / bridge threads) ----

    def start_job(self, message: str, total_steps: int = 5) -> str:
        """Register a QUEUED spike echo job and spawn its worker; returns ``job_id``."""
        return self._launch(
            target=run_echo_job,
            args=None,  # filled below after queue/event are created
            total_steps=total_steps,
            echo_message=message,
        )

    def start_profile_job(
        self,
        *,
        dataset_id: str,
        source_path: str,
        config_document: dict,
        run_id: str,
        profile_mode: str | None = None,
        allowed_roots: list[str] | None = None,
        data_root: str | None = None,
        total_steps: int = 6,
    ) -> str:
        """Register + spawn a REAL profiling job (M5-T2; the §5.2 ``profile.start``).

        Mirrors the job row into the metadata store (``create_job`` in QUEUED) so
        the worker's ``save_run`` can resolve ``runs.job_id`` and status stays
        observable outside this process (§6.3). Returns the ``job_id`` the caller
        polls with ``profile.get_status``."""
        return self._launch(
            target=run_profile_job,
            args=dict(
                dataset_id=dataset_id,
                source_path=source_path,
                config_document=config_document,
                run_id=run_id,
                profile_mode=profile_mode,
                allowed_roots=allowed_roots,
                data_root=data_root,
            ),
            total_steps=total_steps,
            persist=True,
            dataset_id=dataset_id,
            source_path=source_path,
            timeout_seconds=PROFILE_JOB_TIMEOUT_SECONDS,
        )

    def _launch(
        self,
        *,
        target,
        args: dict | None,
        total_steps: int,
        echo_message: str | None = None,
        persist: bool = False,
        dataset_id: str | None = None,
        source_path: str | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        """Shared spawn path for both the spike echo and the real profile worker.

        Builds the QUEUED status slot, then a spawn-process + feeder thread over
        an IPC queue. The only per-job difference is the worker ``target`` and
        its positional args (all picklable primitives — ADR-006); the event-fold
        machinery below is target-agnostic."""
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        record = _JobRecord(
            status=JobStatus(
                job_id=job_id,
                status=JobState.QUEUED,
                progress=JobProgress(total_steps=total_steps),
            ),
            persist=persist,
        )
        with self._lock:
            self._jobs[job_id] = record

        # Persist the QUEUED job row before the worker can start writing runs
        # against it; the store owns the FKs, so ordering here is load-bearing:
        # the ``jobs.dataset_id`` FK needs a ``datasets`` row, so the source is
        # registered first (a later ``save_run`` refreshes its physical facts).
        if persist and self._store is not None and dataset_id is not None:
            self._store.register_dataset(
                dataset_id=dataset_id,
                name=dataset_id,
                connector=_connector_for(source_path),
                source_ref=source_path or dataset_id,
            )
            self._store.create_job(job_id, dataset_id, command="profile.start", state="QUEUED")

        queue = self._ctx.Queue()
        cancel_event = self._ctx.Event()
        if echo_message is not None:
            worker_args = (job_id, echo_message, total_steps, cancel_event, queue)
        else:
            a = args or {}
            # Positional order matches ``run_profile_job`` exactly (spawn-safe,
            # all primitives — ADR-006).
            worker_args = (
                job_id,
                a["dataset_id"],
                a["source_path"],
                a["config_document"],
                a["run_id"],
                a["profile_mode"],
                a["allowed_roots"],
                a["data_root"],
                cancel_event,
                queue,
            )
        proc = self._ctx.Process(
            target=target,
            args=worker_args,
            daemon=True,
            name=f"profiler-worker-{job_id}",
        )
        with self._lock:
            if record.status.status is not JobState.QUEUED:
                # Cancelled while we were setting up — §6.1 QUEUED → CANCELLED.
                return job_id
            record.process = proc
            record.cancel_event = cancel_event
            record.queue = queue
            record.deadline = _now() + (
                timeout_seconds if timeout_seconds is not None else self._timeout_seconds
            )
            record.thread = threading.Thread(
                target=self._feed, args=(job_id,), daemon=True, name=f"feeder-{job_id}"
            )
        proc.start()
        record.thread.start()
        return job_id

    def _commit(self, record: _JobRecord) -> None:
        """Publish one status transition: mirror it into the store (§6.3) **and**
        queue it for the §5.5 push observers. Called while ``self._lock`` is
        held; neither half may fail a job — the mirror is the durable record and
        the push is only a hint the UI can always replace by polling."""
        self._persist(record)
        self._notify(record)

    def _persist(self, record: _JobRecord) -> None:
        """Mirror the record's current status into the store (no-op for the
        in-memory spike). Must be called while holding ``self._lock``; the
        bounded write keeps a single source of truth for job state (§6.3).

        Two conversions are load-bearing:

        * the spike runtime model is *not* a ``ContractModel`` (it keeps null
          keys), so it becomes the canonical §5.3 model first — the shape
          ``save_job_status`` validates against (absent slots omitted, null
          forbidden);
        * a sqlite connection is single-thread (``MetadataStore`` opens one per
          thread by design), and this runs on a per-job **feeder thread**, so we
          must NOT reuse the main thread's handle — a short-lived handle to the
          same on-disk store is opened and closed here instead."""
        if not (record.persist and self._store is not None):
            return
        status = _canonical_status(record.status)
        db_path = getattr(self._store, "db_path", None)
        if db_path is None or str(db_path) == ":memory:":
            # An in-memory store is only reachable from the thread that made it;
            # the profiling path always uses an on-disk store, so this keeps the
            # (single-threaded) direct-store case working without a reopen.
            self._store.save_job_status(status)
            return
        from data_profiling_engine.storage.metadata_store import MetadataStore

        with MetadataStore(db_path=db_path, migrate=False) as thread_store:
            thread_store.save_job_status(status)

    def get_status(self, job_id: str) -> JobStatus | None:
        """Snapshot of the status slot; None for unknown ids."""
        with self._lock:
            record = self._jobs.get(job_id)
            return record.status.model_copy() if record else None

    def cancel_job(self, job_id: str) -> bool:
        """Set CANCEL_REQUESTED (§6.1/§6.5); returns False for unknown jobs.

        Terminal jobs ignore the request (job already reached its final
        state); the confirmation transition to CANCELLED comes from the
        worker, never from this call.
        """
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return False
            if record.status.status.is_terminal:
                return True
            record.cancel_requested = True
            record.status = record.status.model_copy(
                update={"status": JobState.CANCEL_REQUESTED}
            )
            self._commit(record)
            cancel_event = record.cancel_event
        if cancel_event is not None:
            cancel_event.set()
        return True

    def shutdown(self) -> None:
        """Best-effort teardown of all workers (test/app-exit hygiene)."""
        with self._lock:
            records = list(self._jobs.values())
        for record in records:
            proc = record.process
            if proc is not None and proc.is_alive():
                proc.terminate()
                proc.join(timeout=_FORCE_KILL_GRACE_SECONDS)
                if proc.is_alive():
                    proc.kill()

    def recover_interrupted_jobs(self) -> int:
        """Finalise jobs the store still shows as open (§6.3 "recovery on app
        restart").

        A worker process from a previous run cannot survive a restart, so any
        ``jobs`` row left QUEUED/RUNNING/CANCEL_REQUESTED with no in-memory
        counterpart here was interrupted mid-flight: it is honestly moved to
        FAILED with ``WORKER_CRASHED`` (systemic failure, §6.4) rather than
        pretending to resume. Returns the number of rows reconciled. A no-op
        when no store is wired (the in-memory spike has nothing to recover)."""
        if self._store is None:
            return 0
        recovered = 0
        with self._lock:
            live_ids = set(self._jobs)
        for row in self._store.unfinished_jobs():
            job_id = row["job_id"]
            if job_id in live_ids:
                continue  # owned by this process; not an orphan
            self._store.save_job_status(_canonical_status(JobStatus(
                job_id=job_id,
                status=JobState.FAILED,
                progress=JobProgress(
                    completed_steps=row.get("progress_completed") or 0,
                    total_steps=row.get("progress_total") or 0,
                ),
                error_code=errors.WORKER_CRASHED,
            )))
            recovered += 1
        return recovered

    # ---- feeder thread (one per job) ----

    def _feed(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs[job_id]
        while True:
            batch: list[ProgressEvent | TerminalEvent] = []
            while True:
                try:
                    batch.append(record.queue.get_nowait())
                except (queue_module.Empty, OSError):
                    break
            for event in batch:
                if self._apply(job_id, event):
                    self._reap(job_id)
                    return
            if not batch:
                self._mark_running(job_id)
            proc_alive = record.process.is_alive()
            if not proc_alive:
                # Process exited; buffer drained once more, then decide.
                final_batch: list[ProgressEvent | TerminalEvent] = []
                while True:
                    try:
                        final_batch.append(record.queue.get_nowait())
                    except (queue_module.Empty, OSError):
                        break
                for event in final_batch:
                    if self._apply(job_id, event):
                        self._reap(job_id)
                        return
                self._mark_crashed(job_id)
                return
            if self._deadline_hit(job_id):
                self._enforce_timeout(job_id)
                # Keep feeding: the confirmational terminal event (or crash
                # detection) finalises the transition (§6.5).
            time.sleep(_POLL_INTERVAL_SECONDS)

    def _reap(self, job_id: str) -> None:
        """Join the exited worker after a confirmed terminal transition.

        A CANCELLED/SUCCEEDED confirmation means the worker is done with the
        job; joining bounded keeps ``is_alive()`` truthful for tests/hosts
        without letting a stubborn child block the feeder (§6.5 force-stop).
        """
        with self._lock:
            record = self._jobs[job_id]
            proc = record.process
        if proc is not None and proc.is_alive():
            proc.join(timeout=_FORCE_KILL_GRACE_SECONDS)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1.0)

    def _mark_running(self, job_id: str) -> None:
        """QUEUED → RUNNING on the first sign of worker activity (§6.1)."""
        with self._lock:
            record = self._jobs[job_id]
            if (
                not record.running_marked
                and record.status.status is JobState.QUEUED
                and record.process is not None
                and record.process.is_alive()
            ):
                record.running_marked = True
                record.status = record.status.model_copy(
                    update={"status": JobState.RUNNING}
                )
                self._commit(record)

    def _apply(self, job_id: str, event: ProgressEvent | TerminalEvent) -> bool:
        """Fold one worker event into the status slot; True if terminal."""
        with self._lock:
            record = self._jobs[job_id]
            if record.terminal_seen:
                return True
            if isinstance(event, ProgressEvent):
                record.running_marked = True
                update: dict[str, Any] = {
                    "progress": JobProgress(
                        completed_steps=event.completed_steps,
                        total_steps=event.total_steps,
                        current_step=event.current_step,
                    )
                }
                # A cancel-pending job never slips back to RUNNING (§6.1);
                # progress still updates. Same rule for the timeout path,
                # which also parks in CANCEL_REQUESTED (§6.5).
                if record.status.status is not JobState.CANCEL_REQUESTED:
                    update["status"] = JobState.RUNNING
                record.status = record.status.model_copy(update=update)
                self._commit(record)
                return False
            assert isinstance(event, TerminalEvent)
            record.terminal_seen = True
            update: dict[str, Any] = {"status": event.status}
            if event.result is not None:
                update["result"] = event.result
            if event.status is JobState.FAILED and event.error_code:
                update["error_code"] = event.error_code
            record.status = record.status.model_copy(update=update)
            self._commit(record)
            return True

    def _deadline_hit(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs[job_id]
            hit = (
                _now() > record.deadline
                and not record.terminal_seen
                and record.status.status
                in (JobState.RUNNING, JobState.CANCEL_REQUESTED, JobState.QUEUED)
            )
            if hit and record.status.status is not JobState.CANCEL_REQUESTED:
                # Timeout follows the cancel path first (§6.5).
                record.cancel_requested = True
                record.status = record.status.model_copy(
                    update={"status": JobState.CANCEL_REQUESTED}
                )
                self._commit(record)
                if record.cancel_event is not None:
                    record.cancel_event.set()
            return hit

    def _enforce_timeout(self, job_id: str) -> None:
        """Hard-stop a job that did not stop on the cancel path in time."""
        with self._lock:
            record = self._jobs[job_id]
            proc = record.process
            already_terminal = record.terminal_seen
        if already_terminal or proc is None or not proc.is_alive():
            return
        proc.terminate()
        proc.join(timeout=_FORCE_KILL_GRACE_SECONDS)
        if proc.is_alive():
            proc.kill()
        with self._lock:
            record = self._jobs[job_id]
            if not record.terminal_seen:
                record.terminal_seen = True
                record.status = record.status.model_copy(
                    update={"status": JobState.FAILED, "error_code": errors.JOB_TIMEOUT}
                )
                self._commit(record)

    def _mark_crashed(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs[job_id]
            if record.terminal_seen:
                return
            record.terminal_seen = True
            # Worker died without a confirmed transition — systemic failure
            # (§6.4): job FAILED with registry code WORKER_CRASHED.
            code = (
                errors.JOB_TIMEOUT if _now() > record.deadline else errors.WORKER_CRASHED
            )
            record.status = record.status.model_copy(
                update={"status": JobState.FAILED, "error_code": code}
            )
            self._commit(record)


def _now() -> float:
    return time.monotonic()


def _canonical_status(status: JobStatus):
    """Spike runtime ``JobStatus`` → the canonical §5.3 ``ContractModel``.

    ``MetadataStore.save_job_status`` validates its input against
    ``job_status.schema.json``, which forbids a null ``result``/``error_code``
    (absent slots are omitted, never null) — the property the canonical model
    enforces and the plain spike model does not."""
    from data_profiling_engine.schemas.models import (
        JobProgress as CanonicalProgress,
        JobStatus as CanonicalJobStatus,
    )

    # ``current_step`` is required-string-or-absent in the canonical schema; the
    # spike default ("") means "not reported", so it is OMITTED, never passed null.
    step = status.progress.current_step
    if step:
        progress = CanonicalProgress(
            completed_steps=status.progress.completed_steps,
            total_steps=status.progress.total_steps,
            current_step=step,
        )
    else:
        progress = CanonicalProgress(
            completed_steps=status.progress.completed_steps,
            total_steps=status.progress.total_steps,
        )

    # Absent §5.3 slots are OMITTED, never passed null (the strict
    # ``ContractModel`` rejects an explicit null on a non-nullable key).
    data: dict[str, Any] = {
        "job_id": status.job_id,
        "status": status.status.value if hasattr(status.status, "value") else str(status.status),
        "progress": progress,
    }
    if status.error_code:
        data["error_code"] = status.error_code
    if status.result is not None:
        data["result"] = status.result
    return CanonicalJobStatus.model_validate(data)


def canonical_status_payload(status: JobStatus) -> dict[str, Any]:
    """Runtime ``JobStatus`` → the canonical §5.3 status object, as a dict.

    The single serialisation point for a status: the polled
    ``profile.get_status`` result and every §5.5 push payload come out of here,
    so the two transports are interchangeable for the UI (same keys, absent
    slots omitted rather than null, ``job_id`` always present because a push
    carries no envelope)."""
    return _canonical_status(status).model_dump(mode="json", exclude_none=True)


def _connector_for(source_path: str | None) -> str:
    """Registry connector name for a source path (csv/parquet).

    Only used to populate the ``datasets.connector`` column before the worker
    opens the real relation; an undetectable suffix falls back to a neutral
    ``file`` label rather than raising here — the worker's connector is the
    authority and fails properly with ``SOURCE_NOT_ALLOWED`` if the source is
    genuinely unsupported (ADR-011)."""
    if not source_path:
        return "file"
    try:
        from data_profiling_engine.connectors import connector_for_path

        return connector_for_path(source_path)
    except Exception:  # noqa: BLE001 — registration must not block on detection
        return "file"
