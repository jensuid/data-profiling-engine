"""Cancellation-latency + worker-crash-recovery measurement (M7-T2).

TESTING §4.2 rows **Cancellation latency** and **Recovery** and §4.3's binding
structural guarantee — *"Cancellation must complete within the worker's
per-step boundary (bounded), not only at job end."* The M7-T1 baseline runs the
engine spine **in-process** (``measure_and_emit`` → ``profile_dataset``), so
there is no cancel path and no worker to crash; that gap is exactly what this
module closes. It drives the real store-backed worker
(:meth:`JobManager.start_profile_job`, ADR-006) — never ``profile_dataset``
directly — because the guarantee is about the *worker-process* boundary.

Not a packaged-only §5.1 gate: ``multiprocessing`` spawn semantics and the
store-backed manager behave the same in dev and packaged; only RSS measurement
is platform-affected (Windows lacks ``resource``), and cancel/recovery is not.
STATE's open question is therefore resolved in favour of the dev-mode path —
this file, the tests in ``test_benchmark_cancel_recovery.py``, and the
``guarantees`` CLI subcommand are the whole M7-T2b deliverable.

Both measurements are structured as *pure verdict* + *integration driver*
mirroring :mod:`harness`: ``evaluate_cancellation_bounded`` /
``evaluate_recovery_consistent`` are pure and unit-tested without spawning a
worker; ``measure_cancellation_latency`` / ``measure_worker_crash_recovery``
spawn real workers and are exercised as pytest integration tests (a small
generated fixture keeps each case ≲ 20 s) and as the release-gate
``benchmark guarantees`` run (TESTING §1: scheduled, not per-commit).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_profiling_engine.bridge import errors
from data_profiling_engine.bridge.worker.manager import JobManager
from data_profiling_engine.bridge.worker.models import JobState
from data_profiling_engine.bridge.worker.profile_job import PROFILING_TOTAL_STEPS

#: Upper wall-clock bound on one measurement run (matches M5-T2 integration
#: ceiling in ``test_bridge_profile.POLL_SECONDS``); a job that has not
#: reached a terminal state after this is reported as ``deadline_exceeded``,
#: never as success — a stuck worker is a bug, not a pass.
DEFAULT_DEADLINE_SECONDS = 90.0
DEFAULT_POLL_INTERVAL = 0.05


# -- pure verdicts (no worker, unit-testable) -------------------------------

def evaluate_cancellation_bounded(measurement: Dict[str, Any]) -> Dict[str, Any]:
    """Pure §4.3 structural verdict for one cancellation measurement.

    The guarantee holds iff the job reached the CANCELLED terminal state at a
    step boundary **strictly before** its last step — i.e. the worker stopped
    early, not at job end. Verdicts:

    - ``bounded`` — CANCELLED with ``steps_completed < total_steps`` (binding
      guarantee satisfied).
    - ``at_job_end`` — CANCELLED but with ``steps_completed >= total_steps``
      (the worker only observed the flag after the final step; regression
      signal: the boundary check moved out of the loop).
    - ``not_cancelled`` — some other terminal state (SUCCEEDED/FAILED) won the
      race; the measurement is inconclusive (usually fixture too small).
    - ``deadline_exceeded`` — no terminal state reached before the deadline.
    - ``insufficient_data`` — measurement is missing final state or progress.

    Never a numeric target (§4.3: "No universal time limit is set before
    baseline benchmarks"), only the structural one the spec binds now."""
    state = measurement.get("final_state")
    completed = measurement.get("steps_completed")
    total = measurement.get("total_steps", PROFILING_TOTAL_STEPS)
    latency = measurement.get("latency_s")
    if state is None or completed is None:
        return {"verdict": "insufficient_data", "latency_s": latency}
    if state != str(JobState.CANCELLED.value):
        return {
            "verdict": "not_cancelled",
            "final_state": state,
            "error_code": measurement.get("error_code"),
            "latency_s": latency,
        }
    verdict = "bounded" if completed < total else "at_job_end"
    return {
        "verdict": verdict,
        "final_state": state,
        "steps_completed": completed,
        "total_steps": total,
        "latency_s": latency,
    }


def evaluate_recovery_consistent(measurement: Dict[str, Any]) -> Dict[str, Any]:
    """Pure §4.2 verdict for one worker-crash recovery measurement.

    "Worker crash leaves metadata consistent, no corruption" holds iff:

    - the live manager surfaced the crash as ``FAILED`` + ``WORKER_CRASHED``
      (not ``SUCCEEDED``, not stuck);
    - the store row for that job agrees (single source of truth, §6.3);
    - no orphan ``runs`` row exists (the run is only persisted at the final
      step; a killed worker never gets there);
    - a **fresh** manager on the same store reconciles zero open jobs —
      everything the crashed run wrote was already mirrored, so
      ``recover_interrupted_jobs`` has nothing left to fix (idempotent
      recovery, §6.3 "recovery on app restart").

    Any individual failure names itself as the verdict so a baseline reader
    sees exactly which invariant broke."""
    live_state = measurement.get("live_final_state")
    live_code = measurement.get("live_error_code")
    store_state = measurement.get("store_state")
    store_code = measurement.get("store_error_code")
    orphans = measurement.get("orphan_runs")
    leftover = measurement.get("unfinished_after_recover")
    problems: List[str] = []
    if live_state != str(JobState.FAILED.value):
        problems.append(f"live_final_state={live_state!r} expected FAILED")
    if live_code != errors.WORKER_CRASHED:
        problems.append(f"live_error_code={live_code!r} expected WORKER_CRASHED")
    if store_state != str(JobState.FAILED.value):
        problems.append(f"store_state={store_state!r} expected FAILED")
    if store_code != errors.WORKER_CRASHED:
        problems.append(f"store_error_code={store_code!r} expected WORKER_CRASHED")
    if orphans not in (0, None):
        problems.append(f"orphan_runs={orphans} expected 0")
    if leftover != 0:
        problems.append(f"unfinished_after_recover={leftover} expected 0")
    return {
        "verdict": "consistent" if not problems else "inconsistent",
        "problems": problems,
        "measurement": {
            "live_final_state": live_state,
            "live_error_code": live_code,
            "store_state": store_state,
            "store_error_code": store_code,
            "orphan_runs": orphans,
            "unfinished_after_recover": leftover,
            "crash_detection_s": measurement.get("crash_detection_s"),
        },
    }


# -- integration drivers (real worker process) ------------------------------

def _wait_state(
    manager: JobManager,
    job_id: str,
    *,
    predicate,
    deadline: float,
    poll_interval: float,
) -> Optional[Any]:
    """Poll until ``predicate(status)`` is True or the wall-clock deadline
    passes; returns the last matching status (or None). Never assumes — the
    feeder thread is authoritative, the process is asynchronous."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        status = manager.get_status(job_id)
        if status is not None and predicate(status):
            return status
        time.sleep(poll_interval)
    return None


def measure_cancellation_latency(
    *,
    manager: JobManager,
    dataset_id: str,
    source_path: "Path | str",
    config_document: Dict[str, Any],
    run_id: str,
    allowed_roots: Optional[List[str]] = None,
    data_root: Optional[str] = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    deadline: float = DEFAULT_DEADLINE_SECONDS,
) -> Dict[str, Any]:
    """Start a real profiling worker, cancel once RUNNING is observed, and
    time the interval from ``cancel_job(...)`` to the CANCELLED terminal.

    Cancelling at RUNNING (not at QUEUED) is what makes the measurement worth
    recording: it forces the request to cross the in-flight compute boundary,
    which is the case §4.3 actually binds. A pre-RUNNING cancel would be
    trivially bounded by the QUEUED→CANCELLED fast path in ``_launch``.

    The measured latency is an *upper bound* on MVP cancel cost —
    ``profile_job.py`` documents that MVP does not interrupt an active DuckDB
    query mid-flight (§6.5 "for DuckDB interrupts the active query" is Phase
    3); the wall time from ``cancel_job`` to CANCELLED is therefore the
    remaining compute of the step that was already executing.
    """
    job_id = manager.start_profile_job(
        dataset_id=dataset_id,
        source_path=str(source_path),
        config_document=config_document,
        run_id=run_id,
        allowed_roots=allowed_roots,
        data_root=data_root,
    )

    running = _wait_state(
        manager,
        job_id,
        predicate=lambda s: s.status in (JobState.RUNNING, JobState.CANCEL_REQUESTED)
        or s.status.is_terminal,
        deadline=deadline,
        poll_interval=poll_interval,
    )
    if running is None:
        return {
            "verdict": "deadline_exceeded",
            "final_state": None,
            "error_code": None,
            "latency_s": None,
            "steps_completed": None,
            "total_steps": PROFILING_TOTAL_STEPS,
            "job_id": job_id,
        }
    if running.status.is_terminal:
        # Job finished on its own before we could cancel — inconclusive.
        return {
            "verdict": "not_cancelled",
            "final_state": str(running.status.value),
            "error_code": running.error_code,
            "latency_s": 0.0,
            "steps_completed": running.progress.completed_steps,
            "total_steps": PROFILING_TOTAL_STEPS,
            "job_id": job_id,
        }

    t0 = time.monotonic()
    manager.cancel_job(job_id)
    final = _wait_state(
        manager,
        job_id,
        predicate=lambda s: s.status.is_terminal,
        deadline=deadline,
        poll_interval=poll_interval,
    )
    latency = time.monotonic() - t0
    if final is None:
        return {
            "verdict": "deadline_exceeded",
            "final_state": None,
            "error_code": None,
            "latency_s": None,
            "steps_completed": None,
            "total_steps": PROFILING_TOTAL_STEPS,
            "job_id": job_id,
        }
    measurement = {
        "final_state": str(final.status.value),
        "error_code": final.error_code,
        "latency_s": round(latency, 4),
        "steps_completed": final.progress.completed_steps,
        "total_steps": PROFILING_TOTAL_STEPS,
        "job_id": job_id,
    }
    # Fold the pure verdict in so the results doc is self-describing.
    measurement.update({"verdict": evaluate_cancellation_bounded(measurement)["verdict"]})
    return measurement


def measure_worker_crash_recovery(
    *,
    manager: JobManager,
    store: Any,
    dataset_id: str,
    source_path: "Path | str",
    config_document: Dict[str, Any],
    run_id: str,
    allowed_roots: Optional[List[str]] = None,
    data_root: Optional[str] = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    deadline: float = DEFAULT_DEADLINE_SECONDS,
) -> Dict[str, Any]:
    """Start a real profiling worker, hard-kill its process once RUNNING is
    observed, and verify §4.2 Recovery: the live manager marks the job
    ``FAILED`` + ``WORKER_CRASHED``, the SQLite mirror agrees, no orphan run
    row exists, and a fresh manager on the same store reconciles zero
    leftovers.

    ``proc.kill()`` is deliberate (not ``terminate()``): it emulates a segfault
    / OOM-killer / power loss — none of which let the worker emit a terminal
    event. The feeder's ``_mark_crashed`` path is what the spec requires to
    catch this (§6.3 "crash detection"); testing it via ``terminate()`` would
    exercise the graceful-shutdown path instead.

    ``store`` is the same :class:`MetadataStore` the manager mirrors through;
    ``data_root`` (if given) is passed to the worker so it writes artifacts
    inside a throwaway directory the caller can inspect/clean.
    """
    job_id = manager.start_profile_job(
        dataset_id=dataset_id,
        source_path=str(source_path),
        config_document=config_document,
        run_id=run_id,
        allowed_roots=allowed_roots,
        data_root=data_root,
    )

    running = _wait_state(
        manager,
        job_id,
        predicate=lambda s: s.status is JobState.RUNNING or s.status.is_terminal,
        deadline=deadline,
        poll_interval=poll_interval,
    )
    if running is None:
        return {
            "verdict": "deadline_exceeded",
            "job_id": job_id,
            "live_final_state": None,
            "live_error_code": None,
            "store_state": None,
            "store_error_code": None,
            "orphan_runs": None,
            "unfinished_after_recover": None,
            "crash_detection_s": None,
        }
    if running.status.is_terminal:
        # Too-fast worker (fixture small enough that it finished before we
        # could kill it) — inconclusive rather than a false negative.
        return {
            "verdict": "not_killed",
            "job_id": job_id,
            "live_final_state": str(running.status.value),
            "live_error_code": running.error_code,
            "store_state": None,
            "store_error_code": None,
            "orphan_runs": None,
            "unfinished_after_recover": None,
            "crash_detection_s": None,
        }

    # Snapshot the process handle via the manager's own record (SLF001 —
    # benchmark introspection, not a supported public path).
    proc = manager._jobs[job_id].process  # noqa: SLF001
    t_kill = time.monotonic()
    proc.kill()
    final = _wait_state(
        manager,
        job_id,
        predicate=lambda s: s.status.is_terminal,
        deadline=deadline,
        poll_interval=poll_interval,
    )
    detection = time.monotonic() - t_kill

    live_state = str(final.status.value) if final is not None else None
    live_code = final.error_code if final is not None else None

    # Read the store mirror (single source of truth; the manager's ``_persist``
    # ran on the feeder thread on the FAILED transition, §6.3).
    row = store.get_job(job_id) or {}
    store_state = row.get("state")
    store_code = row.get("error_code")

    # No run row should exist (the worker only persists at the final step).
    run_row = store.get_run(run_id) if hasattr(store, "get_run") else None
    orphan_runs = 0 if run_row is None else 1

    # A fresh manager on the same store must find nothing to reconcile — the
    # crashed job was already mirrored live, not left open.
    leftover = JobManager(metadata_store=store).recover_interrupted_jobs()

    measurement = {
        "job_id": job_id,
        "live_final_state": live_state,
        "live_error_code": live_code,
        "store_state": store_state,
        "store_error_code": store_code,
        "orphan_runs": orphan_runs,
        "unfinished_after_recover": leftover,
        "crash_detection_s": round(detection, 4),
    }
    measurement["verdict"] = evaluate_recovery_consistent(measurement)["verdict"]
    return measurement


__all__ = [
    "DEFAULT_DEADLINE_SECONDS",
    "evaluate_cancellation_bounded",
    "evaluate_recovery_consistent",
    "measure_cancellation_latency",
    "measure_worker_crash_recovery",
]
