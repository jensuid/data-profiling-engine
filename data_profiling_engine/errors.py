"""Formal error-code registry (CONTRACTS §8; ADR-014).

CONTRACTS §8 is the *only* definition of cross-layer error codes; this module
is its machine-readable mirror. Codes are consistent across the bridge
(``BridgeErrorObject.code``) and job events (``JobStatus.error_code``), so a
single table carries both. The registry is **extensible** — new codes are added
by adding a row here *and* the matching row in ``docs/CONTRACTS.md`` §8 in the
same change (AGENTS §9); nothing downstream may invent a code silently.

M1-T6 promotes the two spike-only codes here as first-class registry entries:

- ``BRIDGE_REQUEST_INVALID`` (introduced M0-T2, ``bridge/errors.py``) — envelope
  or command-payload schema validation failed.
- ``JOB_NOT_FOUND`` (introduced M0-T3) — status/cancel referenced an unknown job id.

The former spike aliases live in :mod:`data_profiling_engine.bridge.errors`,
which now re-exports these canonical ``.value`` strings.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    """CONTRACTS §8 error codes (extensible). Value == the wire string."""

    CONFIG_INVALID = "CONFIG_INVALID"
    DATASET_READ_FAILED = "DATASET_READ_FAILED"
    SOURCE_NOT_ALLOWED = "SOURCE_NOT_ALLOWED"
    SCHEMA_INFERENCE_FAILED = "SCHEMA_INFERENCE_FAILED"
    METRIC_NOT_COMPUTED = "METRIC_NOT_COMPUTED"
    RULE_EXEC_FAILED = "RULE_EXEC_FAILED"
    WORKER_CRASHED = "WORKER_CRASHED"
    JOB_TIMEOUT = "JOB_TIMEOUT"
    BRIDGE_PAYLOAD_TOO_LARGE = "BRIDGE_PAYLOAD_TOO_LARGE"
    BRIDGE_COMMAND_UNKNOWN = "BRIDGE_COMMAND_UNKNOWN"
    ARTIFACT_WRITE_FAILED = "ARTIFACT_WRITE_FAILED"
    # --- promoted in M1-T6 from the M0 spike (see module docstring) ---
    BRIDGE_REQUEST_INVALID = "BRIDGE_REQUEST_INVALID"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"


@dataclass(frozen=True)
class ErrorInfo:
    """Registry metadata for one code (mirrors a CONTRACTS §8 table row)."""

    code: ErrorCode
    layer: str
    meaning: str
    retryable: bool


def _row(code: ErrorCode, layer: str, meaning: str, retryable: bool) -> ErrorInfo:
    return ErrorInfo(code=code, layer=layer, meaning=meaning, retryable=retryable)


# Ordered exactly as the CONTRACTS §8 table; ``JOB_NOT_FOUND`` / ``BRIDGE_REQUEST_INVALID``
# appended as the newly-promoted rows.
ERROR_REGISTRY: dict[ErrorCode, ErrorInfo] = {
    info.code: info
    for info in (
        _row(ErrorCode.CONFIG_INVALID, "host", "Config failed schema validation", False),
        _row(ErrorCode.DATASET_READ_FAILED, "connector", "Source unreadable/missing", True),
        _row(ErrorCode.SOURCE_NOT_ALLOWED, "security", "Path/scope outside allow-list", False),
        _row(ErrorCode.SCHEMA_INFERENCE_FAILED, "discovery", "Could not infer types", False),
        _row(ErrorCode.METRIC_NOT_COMPUTED, "engine", "Metric unsupported for type", False),
        _row(ErrorCode.RULE_EXEC_FAILED, "quality", "Rule could not execute -> status ERROR", True),
        _row(ErrorCode.WORKER_CRASHED, "job", "Worker process died", True),
        _row(ErrorCode.JOB_TIMEOUT, "job", "Exceeded mode timeout", False),
        _row(ErrorCode.BRIDGE_PAYLOAD_TOO_LARGE, "bridge", "Message exceeded cap", False),
        _row(ErrorCode.BRIDGE_COMMAND_UNKNOWN, "bridge", "Command not in allow-list", False),
        _row(ErrorCode.ARTIFACT_WRITE_FAILED, "storage", "Could not persist artifact", True),
        _row(ErrorCode.BRIDGE_REQUEST_INVALID, "bridge", "Envelope/payload schema validation failed", False),
        _row(ErrorCode.JOB_NOT_FOUND, "job", "Status/cancel referenced an unknown job id", False),
    )
}


def lookup(code: "ErrorCode | str") -> Optional[ErrorInfo]:
    """Return registry metadata for a code, or ``None`` if it is not registered."""
    try:
        key = ErrorCode(code) if not isinstance(code, ErrorCode) else code
    except ValueError:
        return None
    return ERROR_REGISTRY.get(key)


def is_retryable(code: "ErrorCode | str") -> bool:
    """Retryability default for a code; unknown codes are treated as non-retryable."""
    info = lookup(code)
    return info.retryable if info else False


def is_registered(code: "ErrorCode | str") -> bool:
    """True when ``code`` is a known CONTRACTS §8 registry code."""
    return lookup(code) is not None


__all__ = [
    "ErrorCode",
    "ErrorInfo",
    "ERROR_REGISTRY",
    "lookup",
    "is_retryable",
    "is_registered",
]
