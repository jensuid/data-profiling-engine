"""Pydantic envelope models for the JS↔Python bridge (CONTRACTS §5.3).

The wire format is fixed and small: an outer ``BridgeRequest`` carrying a
command name, request id and free-form payload; a matching ``BridgeResponse``
whose ``status`` discriminates ok / accepted / error. Payload size is capped
by the facade (CONTRACTS §5.4); field-level caps live in per-command payload
models. Extra fields are forbidden everywhere — SECURITY §3.

M1-T6 freezes the three envelope models as canonical contracts, so they are
imported from :mod:`data_profiling_engine.schemas.models` (validated against
``schemas/bridge_*.schema.json``) rather than redefined here. Only the
*spike-only*, per-command payload models below remain local — they are not part
of CONTRACTS §5.3 (``echo`` / worker-trio stand-ins for the §5.2 commands).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Canonical §5.3 envelope models (single source of truth: schemas/models.py).
from data_profiling_engine.schemas.models import (
    BridgeErrorObject,
    BridgeRequest,
    BridgeResponse,
)

__all__ = [
    "BridgeRequest",
    "BridgeErrorObject",
    "BridgeResponse",
    "EchoPayload",
    "WorkerEchoJobPayload",
    "JobIdPayload",
]


class EchoPayload(BaseModel):
    """Payload schema for the ``echo`` spike command (M0-T2 only).

    ``echo`` is **not** part of the MVP command set in CONTRACTS §5.2 — it
    exists solely to prove the typed-facade pathway on both OSes.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=0)


class WorkerEchoJobPayload(BaseModel):
    """Payload for the ``worker_echo_job`` spike command (M0-T3 only).

    Submits a bounded echo job to the worker process; spike stand-in for the
    async ``profile.start`` shape of CONTRACTS §5.2 (accepted + ``job_id``).
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=0, max_length=8192)
    total_steps: int = Field(default=5, ge=1, le=200)


class JobIdPayload(BaseModel):
    """Payload for ``job.get_status`` / ``job.cancel`` spike commands (M0-T3)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)
