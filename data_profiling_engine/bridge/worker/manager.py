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
"""
from __future__ import annotations

import multiprocessing
import queue as queue_module
import threading
import time
import uuid
from dataclasses import dataclass
from multiprocessing.context import SpawnContext
from typing import Any

from .. import errors
from .events import ProgressEvent, TerminalEvent
from .models import JobProgress, JobState, JobStatus
from .process import run_echo_job

# Spike defaults; per-mode timeouts are wired in M2-T10/M5-T2.
DEFAULT_JOB_TIMEOUT_SECONDS = 300.0
_FORCE_KILL_GRACE_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 0.05


@dataclass
class _JobRecord:
    """Mutable per-job state; every access is guarded by the manager lock."""

    status: JobStatus
    process: Any = None  # SpawnContext.Process | None
    cancel_event: Any = None
    queue: Any = None
    deadline: float = float("inf")
    cancel_requested: bool = False
    terminal_seen: bool = False
    running_marked: bool = False
    thread: threading.Thread | None = None


class JobManager:
    """Registry of in-flight spike jobs (one worker process each)."""

    def __init__(self, timeout_seconds: float = DEFAULT_JOB_TIMEOUT_SECONDS) -> None:
        self._ctx: SpawnContext = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._jobs: dict[str, _JobRecord] = {}
        self._timeout_seconds = timeout_seconds

    # ---- public API (called from facade / bridge threads) ----

    def start_job(self, message: str, total_steps: int = 5) -> str:
        """Register a QUEUED job and spawn its worker; returns ``job_id``."""
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        record = _JobRecord(
            status=JobStatus(
                job_id=job_id,
                status=JobState.QUEUED,
                progress=JobProgress(total_steps=total_steps),
            )
        )
        with self._lock:
            self._jobs[job_id] = record

        queue = self._ctx.Queue()
        cancel_event = self._ctx.Event()
        proc = self._ctx.Process(
            target=run_echo_job,
            args=(job_id, message, total_steps, cancel_event, queue),
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
            record.deadline = _now() + self._timeout_seconds
            record.thread = threading.Thread(
                target=self._feed, args=(job_id,), daemon=True, name=f"feeder-{job_id}"
            )
        proc.start()
        record.thread.start()
        return job_id

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
                return False
            assert isinstance(event, TerminalEvent)
            record.terminal_seen = True
            update: dict[str, Any] = {"status": event.status}
            if event.result is not None:
                update["result"] = event.result
            if event.status is JobState.FAILED and event.error_code:
                update["error_code"] = event.error_code
            record.status = record.status.model_copy(update=update)
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


def _now() -> float:
    return time.monotonic()
