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
does not). The real MVP commands from CONTRACTS §5.2 are all wired here:
``profile.start`` / ``profile.get_status`` / ``profile.cancel`` land on the
store-backed job manager (M5-T2), and the seven read commands
(``dataset.inspect`` / ``dataset.preview`` / ``quality.get_results`` /
``findings.get`` / ``report.get_summary`` / ``report.get_columns`` (ADR-019) /
``artifact.open``) are served from the M3 stores with paging and the §5.4 caps enforced on **both** directions
(request at the envelope, response re-checked before it is handed back) — M5-T1.
Progress (§5.5) is polled here and is authoritative; the optional host→JS push
lives in :mod:`runtime` and emits the *same* §5.3 status object this module
returns, which is what lets UI code stay transport-agnostic — M5-T3.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from . import errors, runtime
from .envelope import (
    PAYLOAD_CAP_BYTES,
    PREVIEW_ROW_CAP,
    ArtifactOpenPayload,
    BridgeErrorObject,
    BridgeRequest,
    BridgeResponse,
    DatasetInspectPayload,
    DatasetPreviewPayload,
    EchoPayload,
    FindingsGetPayload,
    JobIdPayload,
    ProfileStartPayload,
    QualityGetResultsPayload,
    ReportSummaryPayload,
    ReportGetColumnsPayload,
    WorkerEchoJobPayload,
)
from .worker import default_manager
# One §5.3 status shape for both transports (poll here, push in runtime).
from .worker.manager import canonical_status_payload


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
    ONE status schema, transport-agnostic (the only difference is *which*
    manager owns the job). ``canonical_status_payload`` is the same function the
    §5.5 push path uses, so the object the UI polls and the object it is pushed
    are identical in shape — ``job_id`` included, because a push carries no
    envelope."""
    status = manager.get_status(job_id)
    if status is None:
        return _error_result(job_id, errors.JOB_NOT_FOUND, "unknown job id")
    return canonical_status_payload(status)


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


# ---------------------------------------------------------------------------
# §5.2 read commands (M5-T1) — served from the M3 stores / the connector seam.
# Storage/engine modules are imported lazily inside the handlers so importing
# the bridge stays dependency-light (the pure-envelope tests never load DuckDB).
# ---------------------------------------------------------------------------

#: §4.1 filter vocabularies, validated handler-side so a typo'd filter is a
#: typed ``BRIDGE_REQUEST_INVALID`` instead of a silent empty page.
_CHECK_STATUS_FILTERS = frozenset(
    {"PASS", "FAIL", "WARN", "ERROR"}
)  # QualityCheckResult.status
_CHECK_DIMENSION_FILTERS = frozenset(
    {"completeness", "uniqueness", "validity", "consistency", "timeliness", "accuracy_flagged"}
)  # QualityDimension
_FINDING_SEVERITY_FILTERS = frozenset(
    {"critical", "high", "medium", "low", "warning", "info"}
)  # Severity


class _PathNotAllowed(Exception):
    """Internal signal: a ``dataset.*`` path fell outside the declared roots."""


def _resolve_source_path(source_path: str, allowed_roots: "list[str] | None") -> Path:
    """SECURITY §3 path allow-list for the ``dataset.*`` commands: a
    caller-supplied path is scoped to the roots the host/user declared (the
    same policy :class:`~data_profiling_engine.connectors.base.Connector`
    applies), and only when no roots were declared — a file-picker-selected
    path arrives already user-authorised."""
    resolved = Path(source_path).expanduser().resolve()
    for root in allowed_roots or []:
        r = Path(root).expanduser().resolve()
        if resolved == r or r in resolved.parents:
            return resolved
    if allowed_roots:
        raise _PathNotAllowed(source_path)
    return resolved


def _resolve_run_id(store: Any, payload: dict[str, Any]) -> "str | dict[str, Any] | None":
    """Address one run: an explicit ``run_id`` must exist; otherwise the
    dataset's newest run is used. ``""``/``None`` = no stored run at all.

    An unresolvable run reference is reported as ``BRIDGE_REQUEST_INVALID``
    (the registry §8 has no run-address code and is frozen — extending it would
    be a contract change, so the read commands reuse the bridge-layer code
    exactly as ``JOB_NOT_FOUND`` covers an unknown job)."""
    run_id = payload.get("run_id")
    if run_id:
        return run_id if store.get_run(run_id) is not None else None
    dataset_id = payload.get("dataset_id")
    if dataset_id:
        latest = store.latest_run(dataset_id)
        return latest["run_id"] if latest else None
    return ""


def _connector_error_result(exc: BaseException) -> dict[str, Any]:
    """Map a connector failure onto its own §8 registry code (both connector
    error classes carry one; anything else is reported as a read failure).

    The connector's message names a path the *user* selected (never foreign
    input) and is truncated/newline-stripped, so nothing long or structured
    from the driver passes through (SECURITY §3/§5)."""
    code = getattr(exc, "code", None)
    value = getattr(code, "value", None)
    if not isinstance(value, str):
        value = errors.DATASET_READ_FAILED
    message = " ".join(str(getattr(exc, "message", exc)).split())[:200]
    return _error_result("", value, message or "dataset could not be read")


def _json_safe(value: Any) -> Any:
    """Coerce one DuckDB/sqlite cell into a JSON-representable value.

    Decimal keeps its numeric precision as float (parquet DECIMAL columns),
    date/datetime/times go to ISO strings; anything else falls back to text
    rather than crashing the response."""
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _reveal_path(path: Path) -> None:
    """Ask the OS to open/reveal an artifact file (``artifact.open`` ack path).

    Uses the native viewer (no HTTP, no WebView — ADR-005); a failure to launch
    the viewer is cosmetic and must never fail the command or leak details."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S101 — validated store-owned path
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False, capture_output=True)
        else:  # pragma: no cover - Linux is not a target OS
            subprocess.run(["xdg-open", str(path)], check=False, capture_output=True)
    except Exception:  # noqa: BLE001 - the reveal is best-effort, the ack is not
        pass


def _dataset_inspect_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.2 ``dataset.inspect``: schema/types/size metadata for one source.

    Never reads rows — the size/hash tuple comes from the connector's
    pre-compute snapshot and the columns from a ``LIMIT 0`` schema probe
    (ADR-007). When the source has a stored run, its §3.2 inventory (logical /
    semantic types, population row count) is joined in, so the UI can show
    "already profiled" metadata without opening the profile artifact."""
    from data_profiling_engine.connectors import ConnectorError, get_connector
    from data_profiling_engine.schemas.models import SourceSnapshot

    try:
        path = _resolve_source_path(
            payload["source_path"], payload.get("allowed_roots")
        )
    except _PathNotAllowed:
        return _error_result("", errors.SOURCE_NOT_ALLOWED, "path outside allow-list")

    dataset_id = payload.get("dataset_id") or path.stem
    try:
        impl = get_connector(
            dataset_id,
            path,
            allowed_roots=[Path(r) for r in (payload.get("allowed_roots") or [])] or None,
        )
        snapshot = impl.build_snapshot()
        columns = list(impl._probe_schema(Path(snapshot.path))[0])
    except ConnectorError as exc:
        return _connector_error_result(exc)

    store = runtime.get_metadata_store()
    run_key = _resolve_run_id(store, payload)
    if isinstance(run_key, dict):  # pragma: no cover - _resolve_run_id returns str/None
        return run_key
    result: dict[str, Any] = {
        "dataset_id": dataset_id,
        "connector": impl.name,
        "source_ref": Path(snapshot.path).name,
        "size_bytes": snapshot.size_bytes,
        "modified_at": snapshot.modified_at,
        "content_hash": snapshot.content_hash,
        "row_id_policy": snapshot.row_id_policy,
        "column_count": len(columns),
        "columns": [
            {"ordinal": i, "name": name, "physical_type": ptype}
            for i, (name, ptype) in enumerate(columns)
        ],
    }
    if run_key:
        run = store.get_run(run_key)
        if run is not None:
            typed = {
                row["column_name"]: row
                for row in store.column_inventory(run_key)
            }
            for col in result["columns"]:
                row = typed.get(col["name"])
                if row is not None:
                    col["logical_type"] = row["logical_type"]
                    col["semantic_type"] = row["semantic_type"]
            summary = store.profile_summary(run_key)
            if summary is not None and summary.get("row_count") is not None:
                result["row_count"] = summary["row_count"]
                result["row_count_method"] = summary["row_count_method"]
            result["last_run"] = {
                "run_id": run_key,
                "status": run["status"],
                "finished_at": run["finished_at"],
                "config_hash": run["config_hash"],
            }
    return result


def _dataset_preview_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.2 ``dataset.preview``: one bounded page of rows (§5.4 ≤ 200 rows).

    The scan runs in DuckDB and only the requested page materialises in Python
    (ADR-007). Probable-PII values are masked by default (SECURITY §4) on two
    signals: a stored run's discovery ``logical_type`` tag masks the whole
    column, and — for a source that has never been profiled, where no type
    exists to consult — a value matching discovery's own email/phone pattern is
    masked individually. ``mask_pii=False`` is the explicit opt-out the UI must
    label; full values otherwise reach the user only through the JSON artifact
    and ``artifact.open``."""
    import duckdb

    from data_profiling_engine.connectors import ConnectorError, get_connector

    try:
        path = _resolve_source_path(
            payload["source_path"], payload.get("allowed_roots")
        )
    except _PathNotAllowed:
        return _error_result("", errors.SOURCE_NOT_ALLOWED, "path outside allow-list")

    dataset_id = payload.get("dataset_id") or path.stem
    limit = min(int(payload["limit"]), PREVIEW_ROW_CAP)
    offset = int(payload["offset"])
    con = duckdb.connect()
    try:
        impl = get_connector(
            dataset_id,
            path,
            allowed_roots=[Path(r) for r in (payload.get("allowed_roots") or [])] or None,
        )
        handle = impl.register(con)
        rows = con.execute(
            f'SELECT * FROM "{handle.view_name}" LIMIT {limit} OFFSET {offset}'
        ).fetchall()
        total = int(
            con.execute(f'SELECT count(*) FROM "{handle.view_name}"').fetchone()[0]
        )
        # Drop the engine-plumbing row id (ENGINE §5.2): it is not a data column
        # and an addressable internal only matters inside the persisted profile.
        rows = [tuple(r[: -1]) for r in rows]
        names = [name for name, _ in handle.columns]
    except ConnectorError as exc:
        return _connector_error_result(exc)
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001 - closing an in-memory scan is best-effort
            pass

    # Column-level tags: what discovery decided, when it already has.
    pii: dict[str, str] = {}
    patterns: list = []
    if payload["mask_pii"]:
        from data_profiling_engine.discovery.inference import PII_PATTERNS

        patterns = list(PII_PATTERNS)
        store = runtime.get_metadata_store()
        run_key = _resolve_run_id(store, payload)
        if isinstance(run_key, dict):  # pragma: no cover - defensive
            return run_key
        if run_key:
            for row in store.column_inventory(run_key):
                logical = str(row["logical_type"] or "").lower()
                if logical in ("email", "phone"):
                    pii[str(row["column_name"]).lower()] = logical

    from data_profiling_engine.reporting.html_report import mask_value

    masked_by_value: set[str] = set()

    def _cell(value: Any, name: str) -> Any:
        safe = _json_safe(value)
        tag = pii.get(name.lower())
        if tag is None and isinstance(safe, str):
            for label, pattern in patterns:
                if pattern.match(safe.strip()):
                    tag = label
                    masked_by_value.add(name)
                    break
        if tag in ("email", "phone") and safe is not None:
            return mask_value(str(safe), tag)
        return safe

    # Render the page FIRST — cell-level masking is discovered while rendering,
    # and the column descriptors below must report it.
    rendered = [[_cell(v, n) for v, n in zip(r, names)] for r in rows]
    columns = [
        {
            "name": name,
            "physical_type": dict(handle.columns).get(name, ""),
            "logical_type": pii.get(name.lower()),
            "masked": name.lower() in pii or name in masked_by_value,
        }
        for name in names
    ]
    masked = sorted(
        {name for name in names if name.lower() in pii} | masked_by_value
    )
    return {
        "dataset_id": dataset_id,
        "connector": impl.name,
        "columns": columns,
        "rows": rendered,
        "offset": offset,
        "limit": limit,
        "returned": len(rows),
        "total_rows": total,
        "masking_applied": masked,
    }


def _quality_results_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.2 ``quality.get_results``: one paged §4.1 result set.

    The page is addressed by ``rule_id`` (the store's own order) and each row
    is rebuilt through :meth:`MetadataStore.load_check_results`, so what
    crosses the bridge is the contract object — including the FAIL≠ERROR
    pairing rules — never the raw normalised columns (§1)."""
    status = payload.get("status")
    if status is not None and status.upper() not in _CHECK_STATUS_FILTERS:
        return _error_result("", errors.REQUEST_INVALID, "invalid status filter")
    dimension = payload.get("dimension")
    if dimension is not None and dimension not in _CHECK_DIMENSION_FILTERS:
        return _error_result("", errors.REQUEST_INVALID, "invalid dimension filter")
    store = runtime.get_metadata_store()
    run_key = _resolve_run_id(store, payload)
    if isinstance(run_key, dict):  # pragma: no cover - defensive
        return run_key
    if not run_key:
        return _error_result("", errors.REQUEST_INVALID, "no stored run for request")
    filters = {
        "status": status.upper() if status else None,
        "dimension": dimension,
    }
    total = store.count_checks(run_key, **filters)
    page = store.list_checks(run_key, limit=payload["limit"], offset=payload["offset"], **filters)
    ids = [row["rule_id"] for row in page]
    by_id = {c.rule_id: c for c in store.load_check_results(run_key)}
    items = [
        by_id[rid].model_dump(mode="json", exclude_none=True)
        for rid in ids
        if rid in by_id
    ]
    return {
        "run_id": run_key,
        "items": items,
        "page": {"limit": payload["limit"], "offset": payload["offset"], "total": total},
    }


def _findings_get_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.2 ``findings.get``: one paged §4.2 finding set, contract-shaped."""
    severity = payload.get("severity")
    if severity is not None and severity not in _FINDING_SEVERITY_FILTERS:
        return _error_result("", errors.REQUEST_INVALID, "invalid severity filter")
    category = payload.get("category")
    if category is not None and category not in _CHECK_DIMENSION_FILTERS:
        return _error_result("", errors.REQUEST_INVALID, "invalid category filter")
    store = runtime.get_metadata_store()
    run_key = _resolve_run_id(store, payload)
    if isinstance(run_key, dict):  # pragma: no cover - defensive
        return run_key
    if not run_key:
        return _error_result("", errors.REQUEST_INVALID, "no stored run for request")
    filters = {"severity": severity, "category": category}
    total = store.count_findings(run_key, **filters)
    page = store.list_findings(run_key, limit=payload["limit"], offset=payload["offset"], **filters)
    ids = [row["finding_id"] for row in page]
    by_id = {f.finding_id: f for f in store.load_findings(run_key)}
    items = [
        by_id[fid].model_dump(mode="json", exclude_none=True)
        for fid in ids
        if fid in by_id
    ]
    return {
        "run_id": run_key,
        "items": items,
        "page": {"limit": payload["limit"], "offset": payload["offset"], "total": total},
    }


def _report_summary_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.2 ``report.get_summary``: the §2 run record + §3.1 rollup + tallies.

    Counts-only (no bodies) so it always fits the §5.4 cap; the row sets are
    reached through ``quality.get_results`` / ``findings.get``, and the full
    document only through the ``profile_json`` artifact + ``artifact.open`."""
    store = runtime.get_metadata_store()
    run_key = _resolve_run_id(store, payload)
    if isinstance(run_key, dict):  # pragma: no cover - defensive
        return run_key
    run = store.load_profiling_run(run_key) if run_key else None
    if run is None:
        return _error_result("", errors.REQUEST_INVALID, "no stored run for request")
    summary = store.profile_summary(run_key)
    counts = store.counts(run_key)
    doc = run.model_dump(mode="json", exclude_none=True)
    dataset = doc.get("dataset") or {}
    timing = doc.get("timing") or {}
    sampling = doc.get("sampling")
    return {
        "run_id": run_key,
        "schema_version": doc.get("schema_version"),
        "job_id": doc.get("job_id"),
        "dataset_id": dataset.get("dataset_id"),
        "connector": dataset.get("connector"),
        "status": doc.get("status"),
        "config": doc.get("config"),
        "engine_version": (doc.get("engine") or {}).get("engine_version"),
        "started_at": timing.get("started_at"),
        "finished_at": timing.get("finished_at"),
        "duration_ms": timing.get("duration_ms"),
        "sampling_enabled": bool(sampling.get("enabled")) if sampling else None,
        "sample_seed": sampling.get("seed") if sampling else None,
        "row_count": (summary or {}).get("row_count"),
        "row_count_method": (summary or {}).get("row_count_method"),
        "column_count": (summary or {}).get("column_count"),
        "grain": (summary or {}).get("grain"),
        "temporal_coverage": (summary or {}).get("temporal_coverage"),
        "check_status_counts": store.check_status_counts(run_key),
        "finding_count": counts["findings"],
        "check_count": counts["quality_checks"],
        "recommendation_count": counts["recommendations"],
        "error_count": counts["run_errors"],
        "warning_count": counts["run_warnings"],
        "result_refs": doc.get("result_refs") or {},
        "error_log": doc.get("error_log") or [],
    }


def _report_columns_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.2 ``report.get_columns`` (ADR-019): paged §3.2 ``ColumnProfile`` bodies.

    The full column document is not in the metadata store's normalised rows (those
    carry only the scalar metric leaves, a cheap-diff projection — M3-T3), so the
    authoritative source is the persisted ``profile_json`` artifact: the same file
    ``artifact.open`` reveals, read host-side here and **paged by column ordinal**
    so no single response exceeds the §5.4 message cap (the facade re-checks the
    serialized page). Each returned item is rebuilt through the §3.2
    :class:`ColumnProfile` model so what crosses the bridge is the contract object
    — per-metric ADR-015 provenance and the ``not_computed`` + ``reason``
    absence rule are preserved verbatim, never a silent null. Counts and the
    §3.1 scalar rollup stay in ``report.get_summary``; this command adds no metric
    and computes nothing."""
    from data_profiling_engine.schemas.models import ColumnProfile
    from data_profiling_engine.storage.artifact_store import ArtifactStore, ArtifactStoreError

    store = runtime.get_metadata_store()
    run_key = _resolve_run_id(store, payload)
    if isinstance(run_key, dict):  # pragma: no cover - defensive
        return run_key
    if not run_key:
        return _error_result("", errors.REQUEST_INVALID, "no stored run for request")
    ref = store.artifact_ref(run_key, "profile_json")
    if ref is None:
        return _error_result("", errors.REQUEST_INVALID, "profile document not recorded for run")
    try:
        files = ArtifactStore(data_root=runtime.data_root())
        document = json.loads(files.read_text(ref))
    except (ArtifactStoreError, ValueError):
        # A missing/unreadable/undecodable artifact is a read failure, not a
        # crash; the message stays generic (SECURITY §5 — no path or raw bytes).
        return _error_result("", errors.DATASET_READ_FAILED, "profile document could not be read")
    columns = (document.get("profile") or {}).get("columns") or []
    total = len(columns)
    start = int(payload["offset"])
    stop = start + int(payload["limit"])
    items: list[dict[str, Any]] = []
    for col in columns[start:stop]:
        try:
            items.append(
                ColumnProfile.model_validate(col).model_dump(mode="json", exclude_none=True)
            )
        except ValidationError:
            # A stored body that no longer satisfies §3.2 is a contract violation;
            # surface it typed rather than passing a malformed object across (§1).
            return _error_result("", errors.REQUEST_INVALID, "stored column profile invalid")
    return {
        "run_id": run_key,
        "items": items,
        "page": {"limit": payload["limit"], "offset": start, "total": total},
    }


def _artifact_open_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.2 ``artifact.open``: ack an allow-listed artifact (SECURITY §3/§4).

    The locator is a ``kind`` or a store-relative ``ref`` **already recorded in
    ``run_artifacts`` for that run** — never a caller-composed path — and the
    file store re-confines the resolved path inside its own root, so a
    traversal attempt fails closed. No bytes cross the bridge: the reply is the
    ack (§5.2), optionally revealing the file through the OS viewer."""
    from data_profiling_engine.storage.artifact_store import ArtifactStore, ArtifactStoreError

    store = runtime.get_metadata_store()
    run_key = payload["run_id"]
    if store.get_run(run_key) is None:
        return _error_result("", errors.REQUEST_INVALID, "no stored run for request")
    rows = store.list_artifacts(run_key)
    ref = payload.get("ref")
    if ref is not None:
        known = {row["ref_path"] for row in rows}
        if ref not in known:
            return _error_result("", errors.SOURCE_NOT_ALLOWED, "artifact ref not recorded")
        kind = next(
            (row["kind"] for row in rows if row["ref_path"] == ref), payload.get("kind")
        )
    else:
        kind = payload["kind"]
        ref = store.artifact_ref(run_key, kind)
        if ref is None:
            return _error_result("", errors.REQUEST_INVALID, "artifact not recorded for run")
    try:
        files = ArtifactStore(data_root=runtime.data_root())
        path = files.open_path(ref)
    except ArtifactStoreError as exc:
        return _error_result("", exc.code.value, "artifact could not be opened")
    if payload["reveal"]:
        _reveal_path(path)
    return {"run_id": run_key, "kind": kind, "ref": ref, "size_bytes": path.stat().st_size}


# Real MVP command registry (CONTRACTS §5.2) — complete as of M5-T1: the
# async profiling trio (M5-T2) plus the six store-served read commands.
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
    "dataset.inspect": CommandSpec(
        name="dataset.inspect",
        payload_model=DatasetInspectPayload,
        handler=_dataset_inspect_handler,
    ),
    "dataset.preview": CommandSpec(
        name="dataset.preview",
        payload_model=DatasetPreviewPayload,
        handler=_dataset_preview_handler,
    ),
    "quality.get_results": CommandSpec(
        name="quality.get_results",
        payload_model=QualityGetResultsPayload,
        handler=_quality_results_handler,
    ),
    "findings.get": CommandSpec(
        name="findings.get",
        payload_model=FindingsGetPayload,
        handler=_findings_get_handler,
    ),
    "report.get_summary": CommandSpec(
        name="report.get_summary",
        payload_model=ReportSummaryPayload,
        handler=_report_summary_handler,
    ),
    "report.get_columns": CommandSpec(
        name="report.get_columns",
        payload_model=ReportGetColumnsPayload,
        handler=_report_columns_handler,
    ),
    "artifact.open": CommandSpec(
        name="artifact.open",
        payload_model=ArtifactOpenPayload,
        handler=_artifact_open_handler,
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

        # 7b. Response size cap (CONTRACTS §5.4 binds *messages*, so a handler
        #     that assembled an oversized page is refused here rather than
        #     handed to the WebView — the caller must narrow the request).
        try:
            serialized = json.dumps(result, default=str)
        except (TypeError, ValueError):
            serialized = ""
        if len(serialized.encode("utf-8")) > PAYLOAD_CAP_BYTES:
            return _error(
                req.request_id,
                errors.PAYLOAD_TOO_LARGE,
                "response exceeds message cap; narrow the request or page further",
            )

        return BridgeResponse(
            request_id=req.request_id, status="ok", result=result
        ).model_dump(exclude_none=True)


# ---- helpers (underscore-private, unreachable from JS) ----


def _error(request_id: str, code: str, message: str) -> dict[str, Any]:
    return BridgeResponse(
        request_id=request_id,
        status="error",
        # Retryability is the registry's answer (§8), not the bridge's guess: a
        # read/connector failure is retryable, a rejected envelope is not.
        error=BridgeErrorObject(
            code=code, message=message[:512], retryable=errors.is_retryable(code)
        ),
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
