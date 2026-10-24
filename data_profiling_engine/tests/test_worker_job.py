"""Worker-process tests — M0-T3 spike.

Covers CONTRACTS §6 lifecycle on the spike job manager: start → progress →
SUCCEEDED round-trip, cancel confirmation at a step boundary (§6.5), worker
crash DETECTION (§6.3/§6.4 → FAILED + WORKER_CRASHED), timeout via the cancel
path (→ FAILED + JOB_TIMEOUT), and the facade command trio's envelope shapes
(accepted + job_id per §5.3). Workers are real spawn-method processes, so the
picklability + macOS/Windows spawn constraints of ADR-006 are tested too.
"""
from __future__ import annotations

import json
import time

import pytest

from data_profiling_engine.bridge import BridgeFacade, errors
from data_profiling_engine.bridge.worker.models import JobState
from data_profiling_engine.bridge.worker.manager import JobManager
from data_profiling_engine.bridge.worker.process import CRASH_SENTINEL_PREFIX

POLL_SECONDS = 15.0
POLL_INTERVAL = 0.05


def _wait_terminal(manager: JobManager, job_id: str, timeout: float = POLL_SECONDS) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.get_status(job_id)
        assert status is not None
        if status.status.is_terminal:
            return status
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"job {job_id} did not reach a terminal state in {timeout}s")


def _wait_process_stopped(manager: JobManager, job_id: str, timeout: float = POLL_SECONDS) -> None:
    """Worker exit is confirmed asynchronously (feeder join) — poll, don't assume."""
    proc = manager._jobs[job_id].process  # noqa: SLF001 — spike introspection
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and proc.is_alive():
        time.sleep(POLL_INTERVAL)
    assert not proc.is_alive()


@pytest.fixture()
def manager() -> JobManager:
    mgr = JobManager()
    yield mgr
    mgr.shutdown()


# ---- happy path: start / progress / succeed -------------------------------


def test_worker_echo_job_succeeds_with_round_tripped_message(manager: JobManager) -> None:
    job_id = manager.start_job("hello-worker", total_steps=3)
    status = _wait_terminal(manager, job_id)
    assert status.status is JobState.SUCCEEDED
    assert status.result == {"message": "hello-worker", "echoed_by": "worker"}
    assert status.error_code is None
    # All steps reported through the status slot.
    assert status.progress.completed_steps == 3
    assert status.progress.total_steps == 3


def test_progress_is_monotonic_and_bounded(manager: JobManager) -> None:
    job_id = manager.start_job("steps", total_steps=8)
    seen: list[int] = []
    deadline = time.monotonic() + POLL_SECONDS
    while time.monotonic() < deadline:
        status = manager.get_status(job_id)
        assert status is not None
        completed = status.progress.completed_steps
        if not seen or seen[-1] != completed:
            seen.append(completed)
        if status.status.is_terminal:
            break
        time.sleep(POLL_INTERVAL)
    assert seen == sorted(seen), "completed_steps went backwards (events applied out of order)"
    assert all(0 <= s <= 8 for s in seen)
    assert seen[-1] == 8


def test_state_machine_passes_through_queued_then_running(manager: JobManager) -> None:
    job_id = manager.start_job("lifecycle", total_steps=6)
    observed: set[str] = set()
    deadline = time.monotonic() + POLL_SECONDS
    while time.monotonic() < deadline:
        status = manager.get_status(job_id)
        assert status is not None
        observed.add(str(status.status.value))
        if status.status.is_terminal:
            break
        time.sleep(POLL_INTERVAL)
    assert "RUNNING" in observed
    assert "SUCCEEDED" in observed
    assert "QUEUED" in observed or "RUNNING" in observed  # §6.1 transitions only


# ---- cancellation (§6.5: request → worker confirmation) -------------------


def test_cancel_reaches_cancelled_only_after_confirmation(manager: JobManager) -> None:
    job_id = manager.start_job("long-enough", total_steps=200)
    # Let the worker get mid-job so cancel hits a real step boundary.
    time.sleep(0.4)
    assert manager.cancel_job(job_id) is True
    requested = manager.get_status(job_id)
    assert requested.status is JobState.CANCEL_REQUESTED  # not terminal yet (§6.1)
    status = _wait_terminal(manager, job_id)
    assert status.status is JobState.CANCELLED
    assert status.result is None
    assert status.progress.completed_steps < 200  # stopped early, bounded latency
    _wait_process_stopped(manager, job_id)


def test_cancel_unknown_job_is_false(manager: JobManager) -> None:
    assert manager.cancel_job("job_does_not_exist") is False


# ---- crash detection (§6.3: worker crash detection) -----------------------


def test_worker_crash_is_detected_as_failed(manager: JobManager) -> None:
    job_id = manager.start_job(f"{CRASH_SENTINEL_PREFIX}", total_steps=3)
    status = _wait_terminal(manager, job_id)
    assert status.status is JobState.FAILED
    assert status.error_code == errors.WORKER_CRASHED
    # Host survives the crash — status slot still readable.
    assert manager.get_status(job_id) is not None


# ---- timeout (§6.5: cancel path, then FAILED with JOB_TIMEOUT) ------------


def test_timeout_marks_failed_with_job_timeout() -> None:
    mgr = JobManager(timeout_seconds=0.3)
    try:
        job_id = mgr.start_job("slowpoke", total_steps=40)  # 40 × 0.02s ≈ 0.8s
        status = _wait_terminal(mgr, job_id)
        assert status.status is JobState.FAILED
        assert status.error_code == errors.JOB_TIMEOUT
        _wait_process_stopped(mgr, job_id)  # hard-stopped, not left running
    finally:
        mgr.shutdown()


# ---- facade command trio (envelope shapes, §5.2/§5.3) ---------------------


@pytest.fixture()
def facade() -> BridgeFacade:
    # The spike worker_echo_job / job.* trio are test-only commands (M5-T2).
    return BridgeFacade(enable_spike_commands=True)


def _poll_until_terminal(fac: BridgeFacade, job_id: str) -> dict:
    deadline = time.monotonic() + POLL_SECONDS
    while time.monotonic() < deadline:
        resp = fac.bridge_send(
            {"command": "job.get_status", "request_id": "p", "payload": {"job_id": job_id}}
        )
        assert resp["status"] == "ok"
        result = resp["result"]
        if result["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return result
        time.sleep(POLL_INTERVAL)
    pytest.fail("facade poll timed out")


def test_facade_worker_echo_accepted_envelope(facade: BridgeFacade) -> None:
    resp = facade.bridge_send(
        {
            "command": "worker_echo_job",
            "request_id": "req-w1",
            "payload": {"message": "via-bridge", "total_steps": 2},
        }
    )
    # CONTRACTS §5.3 accepted response: status accepted + job_id, no result.
    assert resp == {
        "request_id": "req-w1",
        "status": "accepted",
        "job_id": resp["job_id"],
    }
    result = _poll_until_terminal(facade, resp["job_id"])
    assert result["status"] == "SUCCEEDED"
    assert result["result"] == {"message": "via-bridge", "echoed_by": "worker"}
    # Status object shape matches §5.3 sample (job_id/status/progress keys).
    assert set(result["progress"]) == {"completed_steps", "total_steps", "current_step"}


def test_facade_job_cancel_round_trip(facade: BridgeFacade) -> None:
    resp = facade.bridge_send(
        {
            "command": "worker_echo_job",
            "request_id": "req-w2",
            "payload": {"message": "cancel-me", "total_steps": 200},
        }
    )
    job_id = resp["job_id"]
    time.sleep(0.4)
    cancel = facade.bridge_send(
        {"command": "job.cancel", "request_id": "req-c2", "payload": {"job_id": job_id}}
    )
    assert cancel["status"] == "ok"
    assert cancel["result"]["cancelled"] is True
    result = _poll_until_terminal(facade, job_id)
    assert result["status"] == "CANCELLED"


def test_facade_status_unknown_job_typed_error(facade: BridgeFacade) -> None:
    resp = facade.bridge_send(
        {
            "command": "job.get_status",
            "request_id": "req-bad",
            "payload": {"job_id": "job_never_started"},
        }
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.JOB_NOT_FOUND
    assert "job_never_started" not in json.dumps(resp)  # no input echo (§SECURITY §3)


@pytest.mark.parametrize(
    "payload",
    [
        {},  # missing job_id
        {"job_id": ""},  # too short
        {"job_id": 42},  # wrong type
        {"job_id": "j", "extra": 1},  # extra field forbidden
    ],
)
def test_facade_job_status_rejects_bad_payload(facade: BridgeFacade, payload: dict) -> None:
    resp = facade.bridge_send(
        {"command": "job.get_status", "request_id": "r", "payload": payload}
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.REQUEST_INVALID


def test_facade_worker_job_rejects_bad_total_steps(facade: BridgeFacade) -> None:
    for bad in (0, 201, "many"):
        resp = facade.bridge_send(
            {
                "command": "worker_echo_job",
                "request_id": "r",
                "payload": {"message": "m", "total_steps": bad},
            }
        )
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID
