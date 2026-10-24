"""Bridge-layer error-code aliases (CONTRACTS §5 / §8).

The full, canonical registry now lives in
:data:`data_profiling_engine.errors` (promoted in M1-T6). This module keeps the
short names the bridge facade / job handlers already import (``errors.REQUEST_INVALID``
etc.) as thin aliases onto the canonical ``ErrorCode`` values, so there is a
single source of truth and no drift between bridge and job event codes.
"""
from __future__ import annotations

from data_profiling_engine.errors import (
    ERROR_REGISTRY,
    ErrorCode,
    ErrorInfo,
    is_registered,
    is_retryable,
    lookup,
)

# CONTRACTS §8 (bridge + job codes the facade emits).
COMMAND_UNKNOWN = ErrorCode.BRIDGE_COMMAND_UNKNOWN.value
PAYLOAD_TOO_LARGE = ErrorCode.BRIDGE_PAYLOAD_TOO_LARGE.value
SOURCE_NOT_ALLOWED = ErrorCode.SOURCE_NOT_ALLOWED.value
WORKER_CRASHED = ErrorCode.WORKER_CRASHED.value
JOB_TIMEOUT = ErrorCode.JOB_TIMEOUT.value

# Promoted in M1-T6 from the M0-T2 / M0-T3 spike into the formal registry.
REQUEST_INVALID = ErrorCode.BRIDGE_REQUEST_INVALID.value
JOB_NOT_FOUND = ErrorCode.JOB_NOT_FOUND.value

# Host-side rejection surfaced by the ``profile.start`` handler (M5-T2): an
# invalid §9 config is refused before any worker spawns (AGENTS §5).
CONFIG_INVALID = ErrorCode.CONFIG_INVALID.value

__all__ = [
    "COMMAND_UNKNOWN",
    "PAYLOAD_TOO_LARGE",
    "SOURCE_NOT_ALLOWED",
    "WORKER_CRASHED",
    "JOB_TIMEOUT",
    "REQUEST_INVALID",
    "JOB_NOT_FOUND",
    "CONFIG_INVALID",
    "ErrorCode",
    "ErrorInfo",
    "ERROR_REGISTRY",
    "is_registered",
    "is_retryable",
    "lookup",
]
