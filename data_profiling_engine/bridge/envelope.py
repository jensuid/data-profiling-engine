"""Pydantic envelope models for the JS↔Python bridge (CONTRACTS §5.3).

The wire format is fixed and small: an outer ``BridgeRequest`` carrying a
command name, request id and free-form payload; a matching ``BridgeResponse``
whose ``status`` discriminates ok / accepted / error. Payload size is capped
by the facade (CONTRACTS §5.4); field-level caps live in per-command payload
models. Extra fields are forbidden everywhere — SECURITY §3.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BridgeRequest(BaseModel):
    """Envelope every JS→Python call must match (CONTRACTS §5.3)."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)


class BridgeErrorObject(BaseModel):
    """Error detail; never carries stack traces or raw sensitive values."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str = Field(max_length=512)
    retryable: bool = False


class BridgeResponse(BaseModel):
    """Envelope returned to JS. One of ``result`` or ``error`` is populated.

    ``job_id`` exists for the async commands defined in CONTRACTS §5.2 but is
    not produced by any M0-T2 spike command.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: Literal["ok", "accepted", "error"]
    result: dict[str, Any] | None = None
    job_id: str | None = None
    error: BridgeErrorObject | None = None


class EchoPayload(BaseModel):
    """Payload schema for the ``echo`` spike command (M0-T2 only).

    ``echo`` is **not** part of the MVP command set in CONTRACTS §5.2 — it
    exists solely to prove the typed-facade pathway on both OSes.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=0)
