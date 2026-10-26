"""M7-T5 — cancel latency and progress observability (CONTRACTS §5.3/§6.5).

The packaged build exposed both halves of one gap (M7-T5 run 2): the engine
spine was one blocking call reported as a single ``profiling:metrics`` step, so
a 2 M-row job sat at ``1/6 · 17 %`` for all 248 s, and a cancel issued during
that pass was only observed when it ended — 4 min 18 s later. TESTING §5.1 asks
for a job that is "observable + cancellable".

These tests pin the two mechanisms that close it:

* the engine calls the stage checkpoint *before* every pass, in order, so the
  worker can narrate real boundaries (and raising from it stops the run);
* a cancel aborts the *active* DuckDB query via ``interrupt()``, which the
  worker confirms as CANCELLED — never as WORKER_CRASHED, and never excusing a
  real failure that merely raced a cancel.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import duckdb
import pytest

from data_profiling_engine.bridge.worker.events import ProgressEvent, TerminalEvent
from data_profiling_engine.bridge.worker.models import JobState
from data_profiling_engine.bridge.worker.profile_job import (
    PROFILING_TOTAL_STEPS,
    _interrupted_by_cancel,
    _STAGE_LADDER,
    _start_interrupt_watch,
    run_profile_job,
)
from data_profiling_engine.orchestration.rollup import profile_dataset
from data_profiling_engine.schemas.models import DatasetConfig

FIXTURES = Path(__file__).parent / "fixtures" / "golden"
CSV = FIXTURES / "sales_deals.csv"
CONFIG = FIXTURES / "sales_deals.config.json"

#: Exactly what the engine announces, in order. The worker's ladder adds the
#: validate step before and the reporting/persist steps after.
ENGINE_STAGES = (
    "profiling:discover",
    "profiling:plan",
    "profiling:statistics_missingness",
    "profiling:uniqueness_categorical",
    "profiling:distributions_temporal",
    "quality:rules",
    "findings:build",
    "recommendations:build",
    "profiling:dataset_metrics",
)


def _config() -> DatasetConfig:
    return DatasetConfig.model_validate(json.loads(CONFIG.read_text(encoding="utf-8")))


# ===========================================================================
# 1. The engine announces real stage boundaries
# ===========================================================================


def test_engine_announces_every_stage_in_order() -> None:
    seen: list[str] = []
    bundle = profile_dataset(
        "stage_scan", CSV, _config(), "run-stages", checkpoint=seen.append
    )

    assert tuple(seen) == ENGINE_STAGES
    assert bundle.profile.column_count == 5


def test_the_worker_ladder_covers_every_engine_stage() -> None:
    """A stage the ladder does not know would freeze the bar at that index."""
    for stage in ENGINE_STAGES:
        assert stage in _STAGE_LADDER, stage
    assert _STAGE_LADDER[0] == "profiling:validate_config"
    assert _STAGE_LADDER[-1] == "persist:metadata"
    assert PROFILING_TOTAL_STEPS == len(_STAGE_LADDER) >= len(ENGINE_STAGES)


def test_checkpoint_abort_stops_the_run_at_that_stage() -> None:
    class Stop(RuntimeError):
        pass

    seen: list[str] = []

    def checkpoint(label: str) -> None:
        seen.append(label)
        if label == "quality:rules":
            raise Stop()

    with pytest.raises(Stop):
        profile_dataset("stage_scan", CSV, _config(), "run-abort", checkpoint=checkpoint)

    assert seen[-1] == "quality:rules"
    # Nothing downstream of the abort ran — findings, recommendations and the
    # dataset-scope metric pass never started.
    assert "findings:build" not in seen
    assert "profiling:dataset_metrics" not in seen


# ===========================================================================
# 2. The worker confirms a mid-query cancel as CANCELLED
# ===========================================================================


class _FakeQueue:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object) -> None:
        self.items.append(item)


class _FakeCancelEvent:
    """Minimal stand-in for ``multiprocessing.Event`` (set/never-set)."""

    def __init__(self, pending: bool) -> None:
        self._pending = pending

    def is_set(self) -> bool:
        return self._pending

    def set(self) -> None:
        self._pending = True

    def wait(self, timeout: float | None = None) -> bool:
        return self._pending


def _run_job(
    monkeypatch, tmp_path: Path, cancel: _FakeCancelEvent, *, request_cancel: bool
) -> list[object]:
    """Drive the worker entry point with the engine replaced by a stub that
    dies the way an interrupted DuckDB query dies.

    ``request_cancel`` models the two distinguishable situations: the user pressed
    Cancel while the pass was running (DuckDB's interrupt, → CANCELLED) and a
    DuckDB failure with no cancel behind it (→ FAILED).
    """

    def fake_profile_dataset(*_args, **kwargs):
        kwargs["checkpoint"]("profiling:statistics_missingness")
        if request_cancel:
            cancel.set()
        raise duckdb.InterruptException("INTERRUPT: query was cancelled")

    monkeypatch.setattr(
        "data_profiling_engine.orchestration.rollup.profile_dataset",
        fake_profile_dataset,
    )
    queue = _FakeQueue()
    run_profile_job(
        "job_cancel",
        "ds",
        str(CSV),
        json.loads(CONFIG.read_text(encoding="utf-8")),
        "run-cancel",
        None,
        None,
        str(tmp_path),
        cancel,  # type: ignore[arg-type]
        queue,  # type: ignore[arg-type]
    )
    return queue.items


def test_cancel_mid_query_is_confirmed_as_cancelled(monkeypatch, tmp_path) -> None:
    items = _run_job(monkeypatch, tmp_path, _FakeCancelEvent(pending=False), request_cancel=True)

    terminals = [event for event in items if isinstance(event, TerminalEvent)]
    assert len(terminals) == 1
    assert terminals[0].status is JobState.CANCELLED
    assert terminals[0].error_code is None
    # The boundary that was in flight when the cancel landed is what the bar
    # shows afterwards — not the step before it.
    progress = [e for e in items if isinstance(e, ProgressEvent)]
    assert progress[-1].current_step == "profiling:statistics_missingness"


def test_interrupt_without_a_cancel_request_is_still_a_failure(monkeypatch, tmp_path) -> None:
    """An unrelated DuckDB failure that races nothing must not be excused."""
    items = _run_job(monkeypatch, tmp_path, _FakeCancelEvent(pending=False), request_cancel=False)

    terminals = [event for event in items if isinstance(event, TerminalEvent)]
    assert len(terminals) == 1
    assert terminals[0].status is JobState.FAILED
    assert terminals[0].error_code == "WORKER_CRASHED"


# ===========================================================================
# 3. The mechanism itself: interrupt() really aborts an active query
# ===========================================================================


def test_interrupt_watch_aborts_the_active_query() -> None:
    con = duckdb.connect()
    cancel = threading.Event()
    stop = threading.Event()
    failure: dict[str, BaseException] = {}

    def compute() -> None:
        try:
            con.execute("SELECT sum(sqrt(i)) FROM range(400000000) t(i)").fetchall()
        except BaseException as exc:  # noqa: BLE001 — asserted below
            failure["exc"] = exc

    _start_interrupt_watch(con, cancel, stop)  # type: ignore[arg-type]
    worker = threading.Thread(target=compute)
    worker.start()
    time.sleep(0.5)

    started = time.monotonic()
    cancel.set()
    worker.join(timeout=30)
    elapsed = time.monotonic() - started
    stop.set()
    con.close()

    assert not worker.is_alive(), "the query ignored interrupt()"
    assert elapsed < 5.0, f"interrupt took {elapsed:.1f}s — watchdog did not fire"
    assert isinstance(failure.get("exc"), duckdb.InterruptException)
    assert _interrupted_by_cancel(failure["exc"], cancel) is True  # type: ignore[arg-type]
    assert _interrupted_by_cancel(failure["exc"], threading.Event()) is False  # type: ignore[arg-type]


def test_interrupt_stays_armed_while_the_cancel_pending() -> None:
    """One shot stops ONE query; the metric loop absorbs that and continues.

    ``engine/metrics/profiler.py`` catches ``duckdb.Error`` per metric, so a
    single-shot interrupt only marks one metric ``not_computed`` and the pass
    keeps scanning — measured at 114 s of extra work on the 2 M-row fixture.
    The watchdog must therefore keep interrupting until the job is finished.
    """

    class _CountingCon:
        def __init__(self) -> None:
            self.calls = 0

        def interrupt(self) -> None:
            self.calls += 1

    con = _CountingCon()
    cancel = threading.Event()
    stop = threading.Event()
    thread = _start_interrupt_watch(con, cancel, stop)  # type: ignore[arg-type]

    cancel.set()
    time.sleep(0.7)
    stop.set()
    thread.join(timeout=5)

    assert con.calls >= 2, f"interrupt fired only {con.calls}x — it is not re-armed"
    assert not thread.is_alive()
