"""M5-T3 — progress polling + optional host→JS push over ONE status schema.

CONTRACTS §5.5 fixes the model: because pywebview is request/response
(ADR-003/005), progress is delivered by polling ``profile.get_status``, with an
OPTIONAL ``evaluate_js`` push that must use the same status object shape — "both
paths share one schema so the UI code is transport-agnostic". These tests pin
exactly that, plus the safety properties the push needs to be honest about being
optional:

* a pushed object and a polled object for the same job are *identical* dicts,
  and both validate against ``job_status.schema.json`` (§5.3, no per-transport
  schema fork);
* pushes are coalesced per job and a terminal transition is never dropped behind
  a stale one, while a consumer that missed a step still gets the truth by
  polling;
* a listener/sink fault never fails a job and never surfaces as a job error
  (§5.5: poll is truth, push is a hint);
* a host that installs no sink is completely inert (no dispatcher work, no
  pushes), so a polling-only app pays nothing;
* the WebView sink obeys the §5.4 message cap (drop, never truncate) and emits a
  fixed JS expression whose only interpolated value is our own serialised object
  (SECURITY §3).

No HTTP server, no new §5.2 command, no new §8 error code (ADR-005). Echo jobs
are spawned for real (spawn method) so the transitions come from the same
worker-event path the packaged app uses (ADR-006).
"""
from __future__ import annotations

import json
import time

import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.bridge import BridgeFacade, configure_runtime
from data_profiling_engine.bridge import runtime
from data_profiling_engine.bridge.envelope import PAYLOAD_CAP_BYTES
from data_profiling_engine.bridge.worker.manager import (
    JobManager,
    _JobRecord,
    canonical_status_payload,
)
from data_profiling_engine.bridge.worker.models import JobProgress, JobState, JobStatus

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
POLL_SECONDS = 30.0
POLL_INTERVAL = 0.05


@pytest.fixture()
def data_root(tmp_path):
    # Bind the bridge context (and therefore the push-wired profile manager) to
    # a throwaway root, and always leave the process-wide sink unset afterwards.
    configure_runtime(tmp_path)
    runtime.configure_status_push(None)
    yield tmp_path
    runtime.configure_status_push(None)
    configure_runtime(None)


@pytest.fixture()
def facade() -> BridgeFacade:
    return BridgeFacade()


def _wait_for(predicate, timeout: float = POLL_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(POLL_INTERVAL)
    pytest.fail("condition never became true within the timeout")


def _poll(facade: BridgeFacade, job_id: str) -> dict:
    resp = facade.bridge_send(
        {
            "command": "profile.get_status",
            "request_id": "poll",
            "payload": {"job_id": job_id},
        }
    )
    assert resp["status"] == "ok", resp
    return resp["result"]


def _poll_terminal(facade: BridgeFacade, job_id: str) -> dict:
    deadline = time.monotonic() + POLL_SECONDS
    while time.monotonic() < deadline:
        result = _poll(facade, job_id)
        if result["status"] in TERMINAL_STATES:
            return result
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"job {job_id} never reached a terminal state")


def _no_nulls(node, path="root") -> None:
    assert node is not None, f"{path}: absent slot arrived as null (§5.3 forbids it)"
    if isinstance(node, dict):
        for key, value in node.items():
            _no_nulls(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _no_nulls(value, f"{path}[{index}]")


# ---- one schema, both transports (§5.5) ----------------------------------


def test_pushed_object_is_identical_to_the_polled_one(facade, data_root) -> None:
    """The core §5.5 claim: the UI cannot tell the two transports apart."""
    received: list[dict] = []
    runtime.configure_status_push(received.append)

    manager = runtime.get_profile_manager()
    job_id = manager.start_job(message="push-or-poll", total_steps=4)
    polled = _poll_terminal(facade, job_id)

    # Push delivery is asynchronous (dispatcher thread), so wait for the hint to
    # catch up with what we already know from polling.
    _wait_for(
        lambda: any(
            payload["job_id"] == job_id
            and payload["status"] in TERMINAL_STATES
            for payload in received
        )
    )
    mine = [payload for payload in received if payload["job_id"] == job_id]

    assert mine[-1] == polled, "push and poll must hand out the identical object"
    # Both transports, one schema: every pushed frame is schema-clean…
    for payload in mine:
        assert S.validate("job_status.schema.json", payload) == [], payload
    # …and so is the polled one, with job_id inside (a push carries no envelope).
    assert S.validate("job_status.schema.json", polled) == [], polled
    assert polled["job_id"] == job_id


def test_every_pushed_frame_keeps_absent_slots_omitted(facade, data_root) -> None:
    """A push has no envelope, so it must be self-describing and absence-faithful."""
    received: list[dict] = []
    runtime.configure_status_push(received.append)

    job_id = runtime.get_profile_manager().start_job(
        message="absence-fidelity", total_steps=3
    )
    _poll_terminal(facade, job_id)
    _wait_for(
        lambda: any(
            payload.get("status") in TERMINAL_STATES for payload in received
        )
    )

    for payload in received:
        assert payload["job_id"] == job_id
        assert "status" in payload and "progress" in payload  # schema-required
        _no_nulls(payload)
    # Progress is monotonic per job even under coalescing (never a stale frame
    # delivered after a newer one), so the UI can render it without guarding.
    steps = [payload["progress"]["completed_steps"] for payload in received]
    assert steps == sorted(steps)


# ---- coalescing (§5.5: newest status wins per job) -----------------------


def _record(job_id: str, completed: int, total: int, state: JobState) -> _JobRecord:
    return _JobRecord(
        status=JobStatus(
            job_id=job_id,
            status=state,
            progress=JobProgress(completed_steps=completed, total_steps=total),
        )
    )


def test_progress_bursts_coalesce_to_the_newest_status():
    manager = JobManager()
    delivered: list[dict] = []
    manager.add_status_listener(delivered.append)

    record = _record("job_coalesce", 0, 10, JobState.RUNNING)
    with manager._lock:
        # Queued frames for the same job never stack up: the newest wins.
        for step in range(1, 6):
            record = _record("job_coalesce", step, 10, JobState.RUNNING)
            manager._notify(record)
        assert list(manager._pending) == ["job_coalesce"]
        assert manager._pending["job_coalesce"]["progress"]["completed_steps"] == 5
        final = _record("job_coalesce", 10, 10, JobState.SUCCEEDED)
        manager._notify(final)

    _wait_for(
        lambda: delivered
        and delivered[-1]["status"] == "SUCCEEDED"
        and delivered[-1]["job_id"] == "job_coalesce"
    )
    # A terminal transition is never dropped behind the stale progress frames it
    # raced with, and coalescing means far fewer pushes than transitions.
    assert len(delivered) < 7
    with manager._lock:
        assert manager._pending == {}


def test_a_manager_without_listeners_queues_nothing():
    """A polling-only host pays nothing: no pending map growth, no dispatcher."""
    manager = JobManager()
    assert manager._dispatcher is None
    manager._notify(_record("job_quiet", 1, 4, JobState.RUNNING))
    assert manager._pending == {}
    assert manager._dispatcher is None


# ---- push never touches job state (§5.5: hint, not truth) ----------------


def test_a_faulty_sink_cannot_fail_a_job(facade, data_root) -> None:
    def exploding_sink(status: dict) -> None:
        raise RuntimeError("WebView is gone")

    runtime.configure_status_push(exploding_sink)
    job_id = runtime.get_profile_manager().start_job(
        message="sink-is-dead", total_steps=3
    )

    result = _poll_terminal(facade, job_id)
    assert result["status"] == "SUCCEEDED", result
    assert "error_code" not in result


def test_no_sink_installed_is_inert(data_root) -> None:
    assert runtime.status_push_enabled() is False
    assert runtime._forward_status({"job_id": "x"}) is None  # no raise, no sink


def test_configure_runtime_does_not_reset_the_push_sink(data_root) -> None:
    """The sink belongs to the host's window, not to the data-root context."""
    runtime.configure_status_push(lambda status: None)
    runtime.configure_runtime(data_root)  # rebinds root, discards the manager
    assert runtime.status_push_enabled() is True


def test_each_fresh_profile_manager_observes_pushes_exactly_once(data_root) -> None:
    runtime.configure_status_push(lambda status: None)
    first = runtime.get_profile_manager()
    assert first is runtime.get_profile_manager()  # cached while configured

    runtime.configure_runtime(data_root)  # discards the manager
    second = runtime.get_profile_manager()
    assert second is not first
    # Idempotent attach: exactly one forwarder, so one push per transition.
    assert second._listeners == [runtime._forward_status]


# ---- the WebView sink (evaluate_js transport) ----------------------------


class FakeWindow:
    """Stands in for a pywebview window: records the JS it was asked to run."""

    def __init__(self, *, raises: bool = False) -> None:
        self.scripts: list[str] = []
        self.raises = raises

    def evaluate_js(self, script: str) -> None:
        if self.raises:
            raise RuntimeError("window closed")
        self.scripts.append(script)


def _pushed_object(script: str) -> dict:
    prefix = f"window.{runtime.JS_STATUS_CALLBACK} && {runtime.JS_STATUS_CALLBACK}("
    assert script.startswith(prefix), script
    assert script.endswith(");"), script
    return json.loads(script[len(prefix) : -2])


def test_sink_emits_the_fixed_callback_with_our_own_object():
    window = FakeWindow()
    status = canonical_status_payload(
        JobStatus(
            job_id="job_sink",
            status=JobState.RUNNING,
            progress=JobProgress(completed_steps=2, total_steps=5, current_step="profiling"),
        )
    )
    runtime.window_push_sink(window)(status)

    assert len(window.scripts) == 1
    # Only our serialised status is interpolated; the callback name is a constant.
    assert _pushed_object(window.scripts[0]) == status
    assert runtime.JS_STATUS_CALLBACK == "__dpeJobStatus"


def test_sink_drops_an_oversized_status_instead_of_truncating():
    window = FakeWindow()
    big = canonical_status_payload(
        JobStatus(
            job_id="job_big",
            status=JobState.SUCCEEDED,
            progress=JobProgress(completed_steps=1, total_steps=1),
        )
    )
    big["result"] = {"blob": "x" * (PAYLOAD_CAP_BYTES + 1)}
    assert len(json.dumps(big).encode("utf-8")) > PAYLOAD_CAP_BYTES

    runtime.window_push_sink(window)(big)
    assert window.scripts == []  # §5.4: the cap binds pushes too; polling still works


def test_sink_swallows_a_dead_window_and_unserialisable_status():
    dying = FakeWindow(raises=True)
    runtime.window_push_sink(dying)({"job_id": "job_dead", "status": "RUNNING"})
    assert dying.scripts == []

    window = FakeWindow()
    # ``set`` is not JSON-serialisable — a push failure stays a push failure.
    runtime.window_push_sink(window)({"job_id": "job_bad", "result": {"tags": {"a"}}})
    assert window.scripts == []
