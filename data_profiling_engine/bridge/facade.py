"""Typed, allow-listed pywebview bridge facade (CONTRACTS §5.1; SECURITY §3).

Only a single entry point — :meth:`BridgeFacade.bridge_send` — is exposed to
JS. It accepts an envelope matching :class:`BridgeRequest`, validates it,
resolves the command against the ``COMMANDS`` allow-list, validates the
command-specific payload schema, dispatches, and always returns an envelope
matching :class:`BridgeResponse`. The facade never raises into the WebView;
unknown input, oversized messages, or handler exceptions come back as typed
error objects.

Spike scope (M0-T2/T3): the ``echo`` + worker-trio
(``worker_echo_job`` / ``job.get_status`` / ``job.cancel``) prove the
bridge→spawn-worker→status-slot pathway. They are kept in :data:`SPIKE_COMMANDS`
and only registered when the facade is constructed with
``enable_spike_commands=True`` (the M0 spike test suite does; the packaged app
does not). The real MVP commands from CONTRACTS §5.2 are wired here:
``profile.start`` / ``profile.get_status`` / ``profile.cancel`` land on the
store-backed job manager (M5-T2).
"""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from . import errors, runtime
from .envelope import (
    BridgeErrorObject,
    BridgeRequest,
    BridgeResponse,
    EchoPayload,
    JobIdPayload,
    ProfileStartPayload,
    WorkerEchoJobPayload,
)
from .worker import default_manager

# CONTRACTS §5.4 — 1 MiB per serialised message.
PAYLOAD_CAP_BYTES = 1 * 1024 * 1024


Handler = Callable[[dict[str, Any]], dict[str, Any]]


class _JobIdResult(BaseModel):
    """Internal handler signal: respond ``accepted`` + ``job_id`` (§5.3)."""

    job_id: str


# Private key a start-handler sets to request the §5.3 ``accepted`` envelope;
# status payloads also carry ``job_id`` but must stay plain ``ok`` responses.
_ACCEPTED_SENTINEL = "__start_accepted__"


@dataclass(frozen=True)
class CommandSpec:
    """Registry entry binding a command name to its schema + handler."""

    name: str
    payload_model: type[BaseModel] | None
    handler: Handler


def _echo_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Spike handler — round-trips the message and names the emitter."""
    return {"message": payload["message"], "echoed_by": "bridge"}


def _worker_start_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Submit the spike echo job to the worker process (M0-T3)."""
    job_id = default_manager().start_job(
        message=payload["message"], total_steps=payload["total_steps"]
    )
    return {_ACCEPTED_SENTINEL: True, "job_id": job_id}


def _status_payload(manager: Any, job_id: str) -> dict[str, Any]:
    """Poll the manager's thread-safe status slot (§5.3/§5.5 shape).

    Shared by the spike ``job.get_status`` and the real ``profile.get_status``:
    one status schema, transport-agnostic (the only difference is *which*
    manager owns the job)."""
    status = manager.get_status(job_id)
    if status is None:
        return _error_result(job_id, errors.JOB_NOT_FOUND, "unknown job id")
    result = status.model_dump(mode="json", exclude_none=True)
    result.pop("job_id", None)  # envelope already identifies the job
    return result


def _job_status_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Spike poll — reads the in-memory default manager."""
    return _status_payload(default_manager(), payload["job_id"])


def _job_cancel_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Request cancellation; terminal CANCELLED comes from the worker (§6.5)."""
    if not default_manager().cancel_job(payload["job_id"]):
        return _error_result(payload["job_id"], errors.JOB_NOT_FOUND, "unknown job id")
    return {"cancelled": True}


def _profile_status_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Real poll (§5.2 ``profile.get_status``) — reads the store-backed manager."""
    return _status_payload(runtime.get_profile_manager(), payload["job_id"])


def _profile_cancel_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.2 ``profile.cancel``: set CANCEL_REQUESTED; confirmation is the worker's."""
    if not runtime.get_profile_manager().cancel_job(payload["job_id"]):
        return _error_result(payload["job_id"], errors.JOB_NOT_FOUND, "unknown job id")
    return {"cancelled": True}


def _new_run_id() -> str:
    # A v4 hex is a fine unique id for a bridge job; the app host may pass a
    # time-sortable ULID via ``payload.run_id`` (§1).
    return str(uuid.uuid4())


def _validate_config(document: dict[str, Any], profile_mode: "str | None") -> "str | None":
    """Reject an invalid §9 config *before* any job starts (AGENTS §5).

    Returns ``None`` when valid, else a short safe reason. The mode override is
    folded in so what we validate is exactly what the worker will run; the
    worker re-validates defensively (it cannot trust the host)."""
    from data_profiling_engine import schemas as S
    from data_profiling_engine.schemas.models import DatasetConfig

    check = document
    if profile_mode is not None:
        profiling = dict(document.get("profiling") or {})
        profiling["mode"] = profile_mode
        check = {**document, "profiling": profiling}
    schema_errors = S.validate("dataset_config.schema.json", check)
    if schema_errors:
        return "config failed schema validation"
    try:
        DatasetConfig.model_validate(check)
    except ValidationError:
        return "config failed model validation"
    return None


def _profile_start_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Enqueue a REAL profiling job (§5.2 ``profile.start``) on the worker.

    Validates the config first (rejected config never spawns a worker), then
    hands the source path + config document + allow-list to the store-backed
    manager, which creates the QUEUED job row and spawns the worker. The reply
    is the §5.3 ``accepted`` + ``job_id`` envelope; results are polled, never
    returned here."""
    reason = _validate_config(payload["config"], payload.get("profile_mode"))
    if reason is not None:
        return _error_result("", errors.CONFIG_INVALID, reason)
    root = runtime.data_root()
    job_id = runtime.get_profile_manager().start_profile_job(
        dataset_id=payload["dataset_id"],
        source_path=payload["source_path"],
        config_document=payload["config"],
        run_id=payload.get("run_id") or _new_run_id(),
        profile_mode=payload.get("profile_mode"),
        allowed_roots=payload.get("allowed_roots"),
        data_root=str(root) if root is not None else None,
    )
    return {_ACCEPTED_SENTINEL: True, "job_id": job_id}


COMMANDS: dict[str, CommandSpec] = {
    "profile.start": CommandSpec(
        name="profile.start",
        payload_model=ProfileStartPayload,
        handler=_profile_start_handler,
    ),
    "profile.get_status": CommandSpec(
        name="profile.get_status",
        payload_model=JobIdPayload,
        handler=_profile_status_handler,
    ),
    "profile.cancel": CommandSpec(
        name="profile.cancel",
        payload_model=JobIdPayload,
        handler=_profile_cancel_handler,
    ),
}

# Spike-only commands (M0-T2/T3). Registered only behind ``enable_spike_commands``
# so the packaged facade exposes nothing the §5.2 MVP set does not define.
SPIKE_COMMANDS: dict[str, CommandSpec] = {
    "echo": CommandSpec(name="echo", payload_model=EchoPayload, handler=_echo_handler),
    "worker_echo_job": CommandSpec(
        name="worker_echo_job",
        payload_model=WorkerEchoJobPayload,
        handler=_worker_start_handler,
    ),
    "job.get_status": CommandSpec(
        name="job.get_status",
        payload_model=JobIdPayload,
        handler=_job_status_handler,
    ),
    "job.cancel": CommandSpec(
        name="job.cancel",
        payload_model=JobIdPayload,
        handler=_job_cancel_handler,
    ),
}


def _spike_enabled_from_env() -> bool:
    return os.environ.get("DPE_ENABLE_SPIKE_COMMANDS", "").lower() in {"1", "true", "yes"}


class BridgeFacade:
    """Object passed to ``webview.create_window(js_api=...)``.

    Only ``bridge_send`` is a public callable; pywebview exposes public
    methods on the js_api object to JS as ``window.pywebview.api.<name>``.
    Everything else on this class is underscore-private and therefore not
    reachable from the WebView.
    """

    def __init__(self, *, enable_spike_commands: "bool | None" = None) -> None:
        # Resolve the spike flag: an explicit argument wins; otherwise the
        # ``DPE_ENABLE_SPIKE_COMMANDS`` env (unset in the packaged app ⇒ spike
        # off). Spike commands are a test affordance only (§5.2 MVP set excludes them).
        if enable_spike_commands is None:
            enable_spike_commands = _spike_enabled_from_env()
        self._commands: dict[str, CommandSpec] = dict(COMMANDS)
        if enable_spike_commands:
            self._commands.update(SPIKE_COMMANDS)

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
        spec = self._commands.get(req.command)
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

        # 7. Async-command envelope: a handler that started a job answers
        #    ``accepted`` + ``job_id`` instead of ``ok`` + ``result`` (§5.3).
        job_id = _extract_job_id(result)
        if job_id is not None:
            return BridgeResponse(
                request_id=req.request_id, status="accepted", job_id=job_id
            ).model_dump(exclude_none=True)

        if isinstance(result, dict) and result.get("__bridge_error__"):
            return _error(
                req.request_id, str(result["code"]), str(result["message"])
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


def _error_result(context_id: str, code: str, message: str) -> dict[str, Any]:
    """Handler-level typed error; facade re-wraps it into the envelope."""
    return {"__bridge_error__": True, "code": code, "message": message}


def _extract_job_id(result: dict[str, Any]) -> str | None:
    """Coerce a start-handler ``job_id`` via the internal model (caps apply).

    Strips the private sentinel and the job id from the dict — an accepted
    envelope carries ``job_id`` at top level, not a ``result`` (§5.3).
    """
    if not isinstance(result, dict) or not result.get(_ACCEPTED_SENTINEL):
        return None
    result.pop(_ACCEPTED_SENTINEL, None)
    job_id = result.pop("job_id", None)
    if not isinstance(job_id, str):
        return None
    try:
        return _JobIdResult(job_id=job_id).job_id
    except ValidationError:
        return None


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
