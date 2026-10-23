"""Worker→host IPC events (ADR-006: IPC queue).

Plain picklable dataclasses sent over a ``multiprocessing`` Queue. One event
stream carries progress updates and terminal transitions; the host's feeder
thread folds them into the thread-safe status slot (``manager``).
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import JobState


@dataclass(frozen=True)
class ProgressEvent:
    """A step-boundary progress update from the worker."""

    job_id: str
    completed_steps: int
    total_steps: int
    current_step: str


@dataclass(frozen=True)
class TerminalEvent:
    """Worker-confirmed terminal transition (SUCCEEDED/FAILED/CANCELLED).

    ``error_code`` ∈ error registry CONTRACTS §8 when status is FAILED;
    ``result`` is the bounded job output (spike: echoed message dict).
    """

    job_id: str
    status: JobState
    error_code: str | None = None
    result: dict | None = None
