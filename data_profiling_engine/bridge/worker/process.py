"""Worker-process entry point for the M0-T3 spike (ADR-006).

Runs inside a ``multiprocessing`` child started with the **spawn** method
(macOS + Windows requirement; keeps the spike identical to the packaged
runtime). The spike job is a bounded "echo" plan: N synthetic steps with a
small sleep each, emitting one :class:`ProgressEvent` per step boundary and
a terminal :class:`TerminalEvent` at the end.

Cancellation semantics follow CONTRACTS §6.5: the worker checks the cancel
flag **at step boundaries** (bounded latency, not job-end) and confirms with
``CANCELLED`` — the host never pretends the job stopped before it did.

The function must stay importable by a spawned child (module-level, picklable
args only) and must never touch the WebView or host-only state.
"""
from __future__ import annotations

import os
import time
from multiprocessing.synchronize import Event as BaseEvent
from multiprocessing.queues import Queue

from .events import ProgressEvent, TerminalEvent
from .models import JobState

STEP_SLEEP_SECONDS = 0.02


def run_echo_job(
    job_id: str,
    message: str,
    total_steps: int,
    cancel_event: BaseEvent,
    event_queue: Queue,
) -> None:
    """Spike job body executed in the worker process.

    A ``message`` carrying the :data:`CRASH_SENTINEL_PREFIX` simulates a hard
    worker crash, so crash-detection can be tested through the same start
    path as normal jobs.
    """
    if _is_crash_message(message):
        # Simulate a hard worker crash (segfault-style exit): no terminal
        # event is ever sent — the host must DETECT it (§6.3 crash detection).
        os._exit(9)

    for step in range(1, total_steps + 1):
        if cancel_event.is_set():
            # Confirmed cancellation at a step boundary (CONTRACTS §6.5).
            event_queue.put(
                TerminalEvent(job_id=job_id, status=JobState.CANCELLED)
            )
            return
        time.sleep(STEP_SLEEP_SECONDS)
        event_queue.put(
            ProgressEvent(
                job_id=job_id,
                completed_steps=step,
                total_steps=total_steps,
                current_step=f"echo:step-{step}",
            )
        )

    event_queue.put(
        TerminalEvent(
            job_id=job_id,
            status=JobState.SUCCEEDED,
            result={"message": message, "echoed_by": "worker"},
        )
    )


# Sentinel used by the spike tests/facade to request a crash job. The real
# engine has no such knob — systemic failure comes from unreadable sources.
CRASH_SENTINEL_PREFIX = "__crash__"


def _is_crash_message(message: str) -> bool:
    return message.startswith(CRASH_SENTINEL_PREFIX)
