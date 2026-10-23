"""Typed, allow-listed pywebview bridge facade (CONTRACTS §5.1; SECURITY §3).

Only a single entry point — :meth:`BridgeFacade.bridge_send` — is exposed to
JS. It accepts an envelope matching :class:`BridgeRequest`, validates it,
resolves the command against the ``COMMANDS`` allow-list, validates the
command-specific payload schema, dispatches, and always returns an envelope
matching :class:`BridgeResponse`. The facade never raises into the WebView;
unknown input, oversized messages, or handler exceptions come back as typed
error objects.

Spike scope (M0-T2): the only registered command is ``echo``. The real MVP
commands from CONTRACTS §5.2 land in M5-T1.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from . import errors
from .envelope import (
    BridgeErrorObject,
    BridgeRequest,
    BridgeResponse,
    EchoPayload,
)

# CONTRACTS §5.4 — 1 MiB per serialised message.
PAYLOAD_CAP_BYTES = 1 * 1024 * 1024


Handler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class CommandSpec:
    """Registry entry binding a command name to its schema + handler."""

    name: str
    payload_model: type[EchoPayload] | None
    handler: Handler


def _echo_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Spike handler — round-trips the message and names the emitter."""
    return {"message": payload["message"], "echoed_by": "bridge"}


COMMANDS: dict[str, CommandSpec] = {
    "echo": CommandSpec(name="echo", payload_model=EchoPayload, handler=_echo_handler),
}


class BridgeFacade:
    """Object passed to ``webview.create_window(js_api=...)``.

    Only ``bridge_send`` is a public callable; pywebview exposes public
    methods on the js_api object to JS as ``window.pywebview.api.<name>``.
    Everything else on this class is underscore-private and therefore not
    reachable from the WebView.
    """

    def bridge_send(self, request: Any) -> dict[str, Any]:
        """Single allow-listed entry point from JS. Never raises."""
        # 1. Accept either an already-parsed mapping (pywebview decodes JSON
        #    into a dict before calling) or a raw JSON string (defensive, e.g.
        #    a caller that double-encodes). Anything else is invalid.
        parsed: Any
        if isinstance(request, str):
            try:
                parsed = json.loads(request)
            except json.JSONDecodeError:
                return _error("", errors.REQUEST_INVALID, "request is not valid JSON")
            size = len(request.encode("utf-8"))
        elif isinstance(request, dict):
            parsed = request
            try:
                size = len(json.dumps(request).encode("utf-8"))
            except (TypeError, ValueError):
                return _error(
                    _safe_request_id(request),
                    errors.REQUEST_INVALID,
                    "request is not JSON-serialisable",
                )
        else:
            return _error("", errors.REQUEST_INVALID, "request must be an object")

        # 2. Envelope-level size cap (CONTRACTS §5.4).
        if size > PAYLOAD_CAP_BYTES:
            return _error(
                _safe_request_id(parsed),
                errors.PAYLOAD_TOO_LARGE,
                f"request exceeds {PAYLOAD_CAP_BYTES} byte cap",
            )

        # 3. Envelope schema validation — extra fields forbidden.
        try:
            req = BridgeRequest.model_validate(parsed)
        except ValidationError as exc:
            return _error(
                _safe_request_id(parsed),
                errors.REQUEST_INVALID,
                _first_validation_hint(exc),
            )

        # 4. Command allow-list (SECURITY §3: unknown → BRIDGE_COMMAND_UNKNOWN).
        spec = COMMANDS.get(req.command)
        if spec is None:
            return _error(
                req.request_id,
                errors.COMMAND_UNKNOWN,
                f"command {req.command!r} is not in the allow-list",
            )

        # 5. Command-payload schema validation.
        payload_data = req.payload
        if spec.payload_model is not None:
            try:
                payload_data = spec.payload_model.model_validate(req.payload).model_dump()
            except ValidationError as exc:
                return _error(
                    req.request_id,
                    errors.REQUEST_INVALID,
                    _first_validation_hint(exc),
                )

        # 6. Dispatch. Handler bugs surface as a typed error, never a stack
        #    trace or Python exception in the WebView console.
        try:
            result = spec.handler(payload_data)
        except Exception as exc:  # noqa: BLE001 — boundary must swallow
            return _error(
                req.request_id,
                errors.REQUEST_INVALID,
                f"handler failed: {type(exc).__name__}",
            )

        return BridgeResponse(
            request_id=req.request_id, status="ok", result=result
        ).model_dump(exclude_none=True)


# ---- helpers (underscore-private, unreachable from JS) ----


def _error(request_id: str, code: str, message: str) -> dict[str, Any]:
    return BridgeResponse(
        request_id=request_id,
        status="error",
        error=BridgeErrorObject(code=code, message=message[:512], retryable=False),
    ).model_dump(exclude_none=True)


def _safe_request_id(parsed: Any) -> str:
    if isinstance(parsed, dict):
        rid = parsed.get("request_id")
        if isinstance(rid, str):
            return rid
    return ""


def _first_validation_hint(exc: ValidationError) -> str:
    """Reduce a pydantic error to a safe, non-echoing hint.

    SECURITY §3 forbids interpolating untrusted input into messages we hand
    back to JS, so we surface only the offending field path and error kind
    (never the ``input`` value pydantic captured).
    """
    errs = exc.errors()
    if not errs:
        return "invalid request"
    first = errs[0]
    loc_parts = [str(p) for p in first.get("loc", ()) if p not in ("__root__",)]
    loc = ".".join(loc_parts) or "request"
    kind = str(first.get("type", "invalid"))
    return f"invalid field {loc!r}: {kind}"
