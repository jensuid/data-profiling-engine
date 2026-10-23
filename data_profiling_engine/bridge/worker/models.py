"""Job status + progress models for the worker spike (CONTRACTS §5.3, §6.1–6.2).

Spike scope (M0-T3): a minimal in-memory job lifecycle that honours the
contract state machine and the exact polled-status shape, so the UI/bridge
pathway is test-transport-agnostic before the real job manager (M5-T2)
replaces it. Persistence (SQLite) is deliberately NOT here — ADR-008 landing
is M3-T1; the spike uses a thread-safe in-host status slot instead.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class JobState(str, Enum):
    """Job states exactly as CONTRACTS §6.2 defines them."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
        )


class JobProgress(BaseModel):
    """Progress object mirroring CONTRACTS §5.3 status sample (field-for-field)."""

    model_config = ConfigDict(extra="forbid")

    completed_steps: int = 0
    total_steps: int = 0
    current_step: str = ""


class JobStatus(BaseModel):
    """Polled status object (CONTRACTS §5.3). ``error_code`` carries a §8
    registry code (e.g. ``WORKER_CRASHED``, ``JOB_TIMEOUT``) on failure;
    ``result`` is the bounded job output on success (spike: the echo text)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobState
    progress: JobProgress = JobProgress()
    error_code: str | None = None
    result: dict | None = None
