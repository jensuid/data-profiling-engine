"""Heavy-work worker package — M0-T3 spike (ADR-006; CONTRACTS §6).

Spawn-method ``multiprocessing`` worker + in-host :class:`JobManager` with a
thread-safe status slot. This is the spike skeleton the real job manager
(M5-T2) replaces; the state machine, status shape, and cancel/crash/timeout
semantics already follow CONTRACTS §6.

Note: ARCHITECTURE §3 places the long-term worker entry under
``orchestration/``; the spike lives here, beside the bridge that drives it,
and is folded into ``orchestration/`` when M5-T2 lands.
"""
from __future__ import annotations

import threading

from .manager import DEFAULT_JOB_TIMEOUT_SECONDS, JobManager
from .models import JobProgress, JobState, JobStatus

__all__ = [
    "DEFAULT_JOB_TIMEOUT_SECONDS",
    "JobManager",
    "JobProgress",
    "JobState",
    "JobStatus",
    "default_manager",
]

_MANAGER: JobManager | None = None
_MANAGER_LOCK = threading.Lock()


def default_manager() -> JobManager:
    """Process-wide singleton used by the spike facade handlers."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = JobManager()
        return _MANAGER
