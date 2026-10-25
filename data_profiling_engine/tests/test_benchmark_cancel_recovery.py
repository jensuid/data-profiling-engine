"""M7-T2b — Cancellation latency + worker-crash recovery (TESTING §4.2/§4.3).

Two tiers of test:

- **Pure verdict** tests on :func:`evaluate_cancellation_bounded` /
  :func:`evaluate_recovery_consistent` — cheap, hand-built measurement dicts,
  no worker; these pin the classification logic independently of any timing.
- **Integration** tests drive the real store-backed
  :class:`~data_profiling_engine.bridge.worker.manager.JobManager` and its
  ``start_profile_job`` path (ADR-006, spawn multiprocessing) against a small
  generated fixture. Cancellation is measured on the live worker; recovery
  kills the child process hard (``proc.kill()``, i.e. SIGKILL, not the
  graceful ``terminate()``) to exercise the crash-detection branch in
  ``_mark_crashed`` and prove the metadata store stays consistent.

These are the tests that make M7-T2b dev-mode verifiable (not a §5.1
packaged-only gate): multiprocessing spawn + on-disk SQLite mirror behave
identically in the packaged build; STATE's open question is resolved here.

Runtime envelope: ~10s per integration test on macOS dev hardware, gated by
the module-scoped 5 000-row fixture (small enough to fit the second-scale
cancel/kill window, large enough that the worker is still RUNNING after the
spawn overhead — a smaller fixture races and returns ``not_cancelled`` /
``not_killed``, which are *inconclusive verdicts*, not failures).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from data_profiling_engine.benchmark.cancel_recovery import (
    DEFAULT_DEADLINE_SECONDS,
    evaluate_cancellation_bounded,
    evaluate_recovery_consistent,
    measure_cancellation_latency,
    measure_worker_crash_recovery,
)
from data_profiling_engine.benchmark.generator import DatasetSpec
from data_profiling_engine.benchmark.harness import ensure_fixture
from data_profiling_engine.bridge import configure_runtime, errors
from data_profiling_engine.bridge.worker.manager import JobManager
from data_profiling_engine.storage.metadata_store import MetadataStore

# Small enough to keep integration tests ≲ 15s each, large enough that
# ``profile_dataset`` is still in-flight when cancel/kill lands. Tuned on
# macOS 13.7.8 dev hardware (baseline: 200k → ~63s, so 5k → ~1.5s of compute).
_FIXTURE_ROWS = 5_000


# ---- pure: cancellation verdict -----------------------------------------

def _cancel_measurement(**over: Any) -> Dict[str, Any]:
    base = {
        "final_state": "CANCELLED",
        "error_code": None,
        "latency_s": 1.4,
        "steps_completed": 2,
        "total_steps": 6,
    }
    base.update(over)
    return base


def test_cancel_bounded_when_stopped_before_last_step():
    verdict = evaluate_cancellation_bounded(_cancel_measurement())
    assert verdict["verdict"] == "bounded"
    assert verdict["steps_completed"] == 2
    assert verdict["latency_s"] == 1.4


def test_cancel_at_job_end_flagged_as_regression():
    # completed_steps >= total_steps means the flag check moved out of the
    # step loop — §4.3 requires a per-step boundary, not a job-end observation.
    verdict = evaluate_cancellation_bounded(
        _cancel_measurement(steps_completed=6, total_steps=6)
    )
    assert verdict["verdict"] == "at_job_end"


def test_cancel_not_cancelled_surfaces_winning_state():
    # SUCCEEDED or FAILED before the cancel reaches the worker → the
    # measurement itself is inconclusive, never falsely "bounded".
    verdict = evaluate_cancellation_bounded(
        _cancel_measurement(final_state="SUCCEEDED", steps_completed=6)
    )
    assert verdict["verdict"] == "not_cancelled"
    assert verdict["final_state"] == "SUCCEEDED"


def test_cancel_insufficient_data_when_state_missing():
    assert evaluate_cancellation_bounded({})["verdict"] == "insufficient_data"
    assert evaluate_cancellation_bounded(
        {"final_state": "CANCELLED"}  # no steps_completed
    )["verdict"] == "insufficient_data"


# ---- pure: recovery verdict ---------------------------------------------

def _recovery_measurement(**over: Any) -> Dict[str, Any]:
    base = {
        "live_final_state": "FAILED",
        "live_error_code": errors.WORKER_CRASHED,
        "store_state": "FAILED",
        "store_error_code": errors.WORKER_CRASHED,
        "orphan_runs": 0,
        "unfinished_after_recover": 0,
        "crash_detection_s": 0.12,
    }
    base.update(over)
    return base


def test_recovery_consistent_when_all_invariants_hold():
    verdict = evaluate_recovery_consistent(_recovery_measurement())
    assert verdict["verdict"] == "consistent"
    assert verdict["problems"] == []


@pytest.mark.parametrize(
    "over,expected_substring",
    [
        ({"live_final_state": "SUCCEEDED"}, "live_final_state"),
        ({"live_error_code": errors.JOB_TIMEOUT}, "live_error_code"),
        ({"store_state": "RUNNING"}, "store_state"),
        ({"store_error_code": None}, "store_error_code"),
        ({"orphan_runs": 1}, "orphan_runs"),
        ({"unfinished_after_recover": 2}, "unfinished_after_recover"),
    ],
)
def test_recovery_flags_each_invariant_separately(over, expected_substring):
    verdict = evaluate_recovery_consistent(_recovery_measurement(**over))
    assert verdict["verdict"] == "inconsistent"
    assert any(expected_substring in p for p in verdict["problems"]), verdict["problems"]


# ---- integration: real worker cancel ------------------------------------

@pytest.fixture(scope="module")
def bench_fixture(tmp_path_factory) -> Path:
    """One 5k-row CSV + matching §9 config, generated once per module."""
    workdir = tmp_path_factory.mktemp("m7t2_fixture")
    spec = DatasetSpec(rows=_FIXTURE_ROWS, columns=5, cardinality="medium",
                       missingness=0.1, distribution="normal", seed=7)
    input_path, base_config = ensure_fixture(spec, "csv_m7t2", workdir, "csv")
    assert input_path.is_file() and base_config.is_file()
    return workdir


@pytest.fixture()
def isolated_store(tmp_path):
    """A throwaway user-data root + its store + a manager wired to it.

    ``configure_runtime`` swaps the process-wide bridge context so the
    artifact store + SQLite mirror land inside ``tmp_path`` and never touch
    the OS default; the restore in the teardown mirrors M5-T2's
    ``data_root`` fixture pattern so no later test inherits this manager."""
    configure_runtime(tmp_path)
    store = MetadataStore(data_root=tmp_path)
    manager = JobManager(metadata_store=store)
    yield tmp_path, store, manager
    manager.shutdown()
    configure_runtime(None)


def _fixture_paths(workdir: Path) -> tuple[Path, Path, Dict[str, Any]]:
    input_path = workdir / "csv_m7t2.csv"
    base_config = workdir / "csv_m7t2.config.json"
    config_document = json.loads(base_config.read_text("utf-8"))
    return input_path, base_config, config_document


def test_measure_cancellation_latency_stops_worker_boundedly(isolated_store, bench_fixture):
    """§4.2/§4.3: cancel a RUNNING profile worker, confirm it reaches
    CANCELLED at a step boundary strictly before its last step; the harness
    records the wall-clock latency (no numeric target — §4.3 forbids one)."""
    data_root, _store, manager = isolated_store
    input_path, _base_config, config = _fixture_paths(bench_fixture)
    run_id = f"run_cancel_{int(time.time() * 1000)}"

    result = measure_cancellation_latency(
        manager=manager,
        dataset_id="m7t2_cancel",
        source_path=input_path,
        config_document=config,
        run_id=run_id,
        allowed_roots=[str(bench_fixture)],
        data_root=str(data_root),
        deadline=DEFAULT_DEADLINE_SECONDS,
    )

    # A fixture this size makes `not_cancelled` (worker won the race) unlikely;
    # if it happens on a slow CI box the verdict is honest, not a false pass.
    assert result["verdict"] in {"bounded", "at_job_end"}, result
    assert result["final_state"] == "CANCELLED", result
    assert result["steps_completed"] is not None
    assert result["steps_completed"] < result["total_steps"], (
        "worker only observed cancel at job end — §4.3 per-step guarantee regressed"
    )
    assert result["latency_s"] is not None and result["latency_s"] >= 0
    # The store mirror must agree (§6.3 single source of truth).
    row = _store.get_job(result["job_id"])
    assert row is not None and row["state"] == "CANCELLED"


def test_measure_worker_crash_recovery_leaves_store_consistent(isolated_store, bench_fixture):
    """§4.2 Recovery: hard-kill the RUNNING worker (SIGKILL, no graceful
    shutdown), assert the live manager + SQLite mirror both land on FAILED /
    WORKER_CRASHED, that no orphan ``runs`` row exists, and that a fresh
    manager on the same store finds nothing to reconcile."""
    data_root, store, manager = isolated_store
    input_path, _base_config, config = _fixture_paths(bench_fixture)
    run_id = f"run_crash_{int(time.time() * 1000)}"

    result = measure_worker_crash_recovery(
        manager=manager,
        store=store,
        dataset_id="m7t2_crash",
        source_path=input_path,
        config_document=config,
        run_id=run_id,
        allowed_roots=[str(bench_fixture)],
        data_root=str(data_root),
        deadline=DEFAULT_DEADLINE_SECONDS,
    )

    if result["verdict"] == "not_killed":
        # Fixture was small enough that the run finished before the kill
        # landed — inconclusive, not a false negative. Recorded honestly.
        pytest.skip("worker completed before kill landed; inconclusive on this hardware")
    assert result["live_final_state"] == "FAILED", result
    assert result["live_error_code"] == errors.WORKER_CRASHED, result
    assert result["store_state"] == "FAILED", result
    assert result["store_error_code"] == errors.WORKER_CRASHED, result
    assert result["orphan_runs"] == 0, "a killed worker must not leave a run row behind"
    assert result["unfinished_after_recover"] == 0, (
        "a fresh manager must find nothing to reconcile — the live path already mirrored"
    )
    assert result["verdict"] == "consistent"
    # No artifact directory was created for the crashed run (nothing persisted).
    artifacts_root = Path(data_root) / "artifacts"
    if artifacts_root.exists():
        leaked = [p for p in artifacts_root.rglob("*") if run_id in p.name]
        assert leaked == [], f"partial artifacts from a crashed run: {leaked}"
