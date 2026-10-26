"""SQLite metadata store — dataset registry, config versions, jobs, runs,
findings metadata and artifact references (M3-T1; ADR-008, SECURITY §2/§6.5).

What lives here and what does **not**
------------------------------------
ADR-008 assigns SQLite *metadata* and forbids it from carrying *large blobs*.
So every persisted object is stored **decomposed into columns and child rows**
(a run → ``runs`` + ``dataset_profiles`` + ``run_columns`` + ``quality_checks``
+ ``findings`` + ``recommendations`` + error/warning/artifact child tables),
and the full result document (column metric bodies, histograms, category lists)
stays a file in the artifact store, pointed at by ``run_artifacts``. Normalised
per-metric values are deliberately **not** in this task — that is M3-T3.

Contract obligations carried by the writer
------------------------------------------
* Every object is validated against its canonical JSON Schema **before** it is
  written and again after it is read back, so a stored row can never be a shape
  the contract does not allow (AGENTS §3, TESTING §3.5).
* Absence vs null is preserved. A ``NULL`` column cannot tell "the contract
  document omitted this key" from "the key was there and null", and §1 gives
  those two different meanings (and ``ContractModel`` rejects a null for any
  field its schema does not type as nullable), so every row also stores the
  sorted key set of the document it was written from
  (:func:`_present_keys` / :func:`_retain_present`) and JSON-typed leaves carry
  their own presence marker (:func:`_field_json`). The reader rebuilds exactly
  the keys it was given: no absent key returns as a silent null, no empty list
  collapses into "not reported", and no explicit null disappears. That is what
  makes the round-trip tests meaningful rather than decorative.
* ``method`` provenance is stored beside every numeric the metadata keeps (run
  row/column counts, check observations) so a value is never presented without
  the label of how it was produced (ADR-015).
* FAIL ≠ ERROR: a check row keeps ``status`` and carries ``error_code`` /
  ``error_message`` only when the status is ``ERROR`` (§6.4), and per-task
  errors ride ``run_errors`` without demoting the run.
* Findings keep their ``fingerprint`` indexed but **not** unique — cross-run
  identity is what baseline/drift keys on (ADR-016).

Concurrency (ADR-008 consequences)
----------------------------------
Single-writer discipline: one connection per store, ``isolation_level = None``
and one short ``BEGIN IMMEDIATE`` transaction per write operation, so a run and
all its child rows appear atomically (a crashed worker cannot leave a run
without findings — the store half of "worker crash does not corrupt the
metadata store"). ``journal_mode=WAL`` + ``busy_timeout`` let a reader query
while a writer commits, which is the ADR's "concurrent read during run". Each
*process* opens its own :class:`MetadataStore`; inside one process the
host-side singleton (``bridge.runtime.get_metadata_store``) is reached from
several threads — the ``js_api`` dispatch that serves the WebView, the job
manager's monitor, and test threads — so the single connection is opened
thread-agnostic and every statement is serialised by ``self._lock``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Type,
)

from data_profiling_engine import schemas as S
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.schemas.models import (
    CheckError,
    CheckEvidence,
    CheckThreshold,
    ColumnProfile,
    DatasetConfig,
    Finding,
    FindingFact,
    FindingHypothesis,
    FindingScope,
    JobProgress,
    JobStatus,
    MetricValue,
    ProfilingRun,
    Provenance,
    QualityCheckResult,
    RunConfig,
    RunDataset,
    RunEngine,
    RunErrorEntry,
    RunSampling,
    RunTiming,
    Recommendation,
    SourceSnapshot,
)
from data_profiling_engine.storage.migrations import (
    MIGRATIONS,
    Migration,
    MigrationReport,
    apply_migrations,
    applied_migrations,
    current_version,
)
from data_profiling_engine.storage.paths import (
    BACKUP_SUBDIR,
    DEFAULT_APP_NAME,
    ensure_outside_app_bundle,
    ensure_private_dir,
    metadata_db_path,
    resolve_data_root,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps storage engine-agnostic
    from data_profiling_engine.orchestration.rollup import ProfileBundle

#: Non-terminal §6.2 states — a job row in one of these was still in flight
#: (or was interrupted) the last time the store saw it.
OPEN_JOB_STATES = ("QUEUED", "RUNNING", "CANCEL_REQUESTED")

#: Terminal §6.2 states.
TERMINAL_JOB_STATES = ("SUCCEEDED", "FAILED", "CANCELLED")

#: Default sqlite3 busy-handler window.
DEFAULT_BUSY_TIMEOUT_MS = 5000

#: Artifact kind used when a caller hands a result document path without a kind.
DEFAULT_ARTIFACT_KIND = "profile_json"


class MetadataStoreError(RuntimeError):
    """Storage-layer failure. ``code`` is a CONTRACTS §8 registry code so the
    bridge and job events surface it consistently; the message never carries
    raw data values or credentials (SECURITY §5/§7)."""

    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.METADATA_WRITE_FAILED) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class MetadataIntegrityError(MetadataStoreError):
    """A write or read refused because a document does not satisfy its canonical
    schema — the store is not the place a contract violation gets to hide."""


# ---------------------------------------------------------------------------
# serialisation helpers
# ---------------------------------------------------------------------------


def _now_z() -> str:
    """ISO-8601 UTC ``...Z`` stamp (CONTRACTS §1 timestamps)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump_json(value: Any) -> str:
    """Deterministic text for a JSON-safe value (``sort_keys`` keeps golden
    snapshots and diffs stable — TESTING §3.3)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _opt_json(value: Any) -> Optional[str]:
    """Store a JSON-safe value, mapping *Python* ``None`` to SQL ``NULL``.

    A real JSON ``null`` becomes the text ``"null"``, which stays distinguishable
    from "no value" — the distinction §1 insists on for metric values."""
    if value is None:
        return None
    return _dump_json(value)


def _load_json(text: Optional[str]) -> Any:
    return None if text is None else json.loads(text)


def _field_json(model: Any, name: str) -> Optional[str]:
    """Store one JSON-typed model field, keeping *key absent* distinct from an
    explicitly set value (including an explicit ``null``, which dumps as the
    text ``"null"``).

    ``ContractModel`` treats "never set" and "set to null" as different things
    on the wire, and §1 makes that distinction carry meaning for metric values,
    so a plain ``NULL`` column would silently merge them."""
    value = getattr(model, name, None)
    if value is None and name not in getattr(model, "model_fields_set", set()):
        return None
    return _dump_json(value)


def _apply_json_field(target: Dict[str, Any], text: Optional[str], name: str) -> None:
    """Inverse of :func:`_field_json`: restore the key only when the column
    says it was present, so an absent key is not rebuilt as a null."""
    if text is not None:
        target[name] = json.loads(text)


def _present_keys(document: Mapping[str, Any]) -> str:
    """Sorted key set of a canonical contract document, stored beside the row.

    A column is ``NULL`` both when the engine *omitted* the key and when it
    carried an explicit ``null`` — yet §1 gives those two different meanings,
    and ``ContractModel`` rejects a null for any field the schema does not type
    as nullable. Recording the key set is what lets the reader rebuild the
    document it was given, byte-for-byte (no absent key coming back as a silent
    null, no ``[]`` collapsing into "not reported"), which is what the
    round-trip tests assert."""
    return _dump_json(sorted(document))


def _retain_present(kwargs: Dict[str, Any], marker: Optional[str]) -> Dict[str, Any]:
    """Keep only the keys the stored document actually carried (the inverse of
    :func:`_present_keys`). A missing marker (``NULL``) means "written without a
    key record", so nothing is filtered out."""
    if marker is None:
        return kwargs
    present = set(json.loads(marker))
    return {key: value for key, value in kwargs.items() if key in present}


def _model_doc(obj: Any) -> Dict[str, Any]:
    """Contract model → JSON-safe document (same dump the JSON writer uses)."""
    return obj.model_dump(mode="json")


def _build_model(cls, **kwargs: Any):
    """Rebuild a strict contract model from columns, honouring the
    ``ContractModel`` null/absence policy: a ``None`` for a field the canonical
    schema does NOT type as nullable is dropped (key absent), while a field in
    ``NULLABLE_FIELDS`` keeps its explicit null (same rule
    ``orchestration.rollup._instantiate`` applies)."""
    nullable = cls.NULLABLE_FIELDS
    data = {k: v for k, v in kwargs.items() if v is not None or k in nullable}
    return cls.model_validate(data)


def _validate(filename: str, document: Any, *, context: str) -> None:
    """Schema gate at the storage boundary (TESTING §3.5 + §1 integration row
    "SQLite persistence": nothing may be stored in a shape the contract does
    not define)."""
    errors = S.validate(filename, document)
    if errors:
        raise MetadataIntegrityError(
            f"{context} failed {filename} validation: " + "; ".join(errors[:5])
        )


def _rows(cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
    return [dict(zip((d[0] for d in cursor.description), row)) for row in cursor.fetchall()]


#: The CONTRACTS §3.2 ``MetricValue`` leaves of a ColumnProfile, in a fixed
#: (deterministic) order, as ``(metric_name, getter)`` pairs. ``metric_name`` is
#: the §3.2 path so a normalised row addresses exactly the cell a diff and a
#: Finding ``metric_ref`` both name. Only ``value``+provenance pairs are
#: promoted — histogram bins, category arrays and the plain int counters stay
#: in the artifact (ADR-008: SQLite holds metadata, not result blobs).
_STATS_METRIC_FIELDS = (
    "min", "max", "mean", "median", "p25", "p75", "p95", "p99", "stddev",
)


def _iter_column_metrics(column: ColumnProfile) -> List[Tuple[str, MetricValue]]:
    """Flatten one ColumnProfile to ``(metric_name, MetricValue)`` pairs.

    Enumerated explicitly against the model rather than by generic recursion so
    a provenance-bearing non-scalar (histogram, outlier block) is never
    mistaken for a metric cell, and so an added §3.2 metric is an intentional
    one-line change here rather than an accidental row appearing in a diff."""
    out: List[Tuple[str, MetricValue]] = []
    if column.missingness is not None:
        out.append(("missingness.missing_rate", column.missingness.missing_rate))
    if column.uniqueness is not None:
        out.append(("uniqueness.distinct_count", column.uniqueness.distinct_count))
    if column.stats is not None:
        for name in _STATS_METRIC_FIELDS:
            value = getattr(column.stats, name)
            if value is not None:
                out.append((f"stats.{name}", value))
    if column.distribution is not None and column.distribution.skewness is not None:
        out.append(("distribution.skewness", column.distribution.skewness))
    return out


def config_document_hash(config: DatasetConfig) -> str:
    """``sha256:<hex>`` over a §9 config model — the CONTRACTS §2
    ``config.config_hash`` digest.

    Deliberately identical to ``orchestration.rollup.config_hash``
    (``model_dump_json`` walks fields in declaration order, so one logical
    config has exactly one digest). Storage recomputes it rather than importing
    the engine, keeping the layer free of DuckDB/pipeline dependencies; the
    parity is pinned by ``test_standalone_digest_matches_rollup_config_hash``,
    so a registry row and the run row that cites it can never disagree."""
    digest = hashlib.sha256(config.model_dump_json().encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------


class MetadataStore:
    """Typed writer/reader for the SQLite metadata database (ADR-008).

    Opening a store *is* the migration entry point: the schema is brought
    forward (with a pre-migration backup) before the handle is usable, which is
    the SECURITY §6.5 "migrations run forward on first launch of a new version"
    behaviour the CLI, host and worker all inherit for free."""

    def __init__(
        self,
        db_path: "Path | str | None" = None,
        *,
        data_root: "Path | str | None" = None,
        app_name: str = DEFAULT_APP_NAME,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        migrate: bool = True,
        backup_before_migrate: bool = True,
        migrations: Sequence[Migration] = MIGRATIONS,
    ) -> None:
        """``db_path``: explicit file (or ``":memory:"``); when omitted the
        database lives in the OS-standard user-data directory (SECURITY §2),
        never inside the app bundle. ``data_root`` redirects that resolution
        (tests, per-user packaging)."""
        in_memory = db_path is not None and str(db_path) == ":memory:"
        self._data_root = resolve_data_root(data_root, app_name=app_name)
        if db_path is None:
            path = metadata_db_path(self._data_root)
        elif in_memory:
            path = Path(":memory:")
        else:
            path = Path(str(db_path)).expanduser()
            path = path.parent.resolve() / path.name
        if not in_memory:
            ensure_private_dir(path.parent)
            ensure_outside_app_bundle(path, label="metadata database")
        self.db_path = path
        self._lock = threading.RLock()
        # sqlite3 binds a connection to its creating thread by default; the one
        # process-wide store is legitimately used from the bridge dispatch
        # thread, the job-manager monitor and test threads (ADR-005/006). Every
        # statement goes through ``_query``/``_write_tx`` under ``self._lock``,
        # so sharing the handle across threads stays serialised.
        self._conn = sqlite3.connect(
            str(path), timeout=busy_timeout_ms / 1000.0, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        # Explicit transaction control only: the legacy implicit mode commits
        # around DDL/BEGIN and would break the atomic-per-operation promise.
        self._conn.isolation_level = None
        self._conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL: a reader (UI, another process) never blocks the single writer.
        self._enable_wal(self._conn, busy_timeout_ms)
        self.migration_report: Optional[MigrationReport] = None
        if migrate:
            self.migration_report = apply_migrations(
                self._conn,
                migrations=tuple(migrations),
                backups_root=self._backups_root() if backup_before_migrate else None,
            )

    # -- plumbing -----------------------------------------------------------

    @staticmethod
    def _enable_wal(conn: sqlite3.Connection, busy_timeout_ms: int) -> None:
        """Put the database in WAL mode, tolerating a concurrent opener.

        Switching the journal mode needs the file exclusively for a moment and
        SQLite does **not** run the busy handler for that PRAGMA, so two
        processes opening the same store at the same time (host + worker,
        ADR-006) can otherwise fail with a plain ``database is locked``. The
        mode is a persistent property of the database, so only the loser of that
        race needs to retry; if the file already reports WAL the goal is met by
        the winner's change. Anything else is raised — a store that stays in
        ``delete`` mode would block the UI on every write."""
        deadline = time.monotonic() + max(0.0, busy_timeout_ms / 1000.0)
        while True:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                return
            except sqlite3.OperationalError:
                if time.monotonic() >= deadline:
                    row = conn.execute("PRAGMA journal_mode").fetchone()
                    mode = str(row[0]).lower() if row else "unknown"
                    if mode == "wal":
                        return
                    raise MetadataStoreError(
                        f"could not switch the metadata database to WAL mode (current: {mode})"
                    ) from None
                time.sleep(0.02)

    def _backups_root(self) -> Optional[Path]:
        """Pre-migration backups live beside the database, inside the same
        user-data directory, so an upgrade (which replaces only the app dir)
        carries them along (SECURITY §2, §6.5)."""
        if str(self.db_path) == ":memory:":
            return None
        return self.db_path.parent / BACKUP_SUBDIR

    @property
    def data_root(self) -> Path:
        """User-data directory this store resolved against (SECURITY §2)."""
        return self._data_root

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def schema_version(self) -> int:
        with self._lock:
            return current_version(self._conn)

    def migration_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return applied_migrations(self._conn)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "MetadataStore":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    @contextmanager
    def _write_tx(self) -> Iterator[sqlite3.Connection]:
        """One short ``BEGIN IMMEDIATE`` write transaction (ADR-008: the worker
        writes via short transactions, never a long-held write lock).

        A driver failure is reported as :class:`MetadataStoreError` carrying a
        §8 code (layer ``storage``) rather than leaking ``sqlite3`` internals
        across the bridge boundary (SECURITY §7: diagnostics stay registry
        shaped); the transaction is always rolled back first."""
        with self._lock:
            if self._conn is None:
                raise MetadataStoreError("metadata store is closed")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except sqlite3.Error as exc:
                self._conn.execute("ROLLBACK")
                raise MetadataStoreError(f"metadata write failed: {exc}") from exc
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            if self._conn is None:
                raise MetadataStoreError("metadata store is closed")
            return _rows(self._conn.execute(sql, tuple(params)))

    def _query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # -- dataset registry ---------------------------------------------------

    def register_dataset(
        self,
        *,
        dataset_id: str,
        name: str,
        connector: str,
        source_ref: str,
        source_kind: str = "file",
        content_hash: Optional[str] = None,
        size_bytes: Optional[int] = None,
        modified_at: Optional[str] = None,
        grain: Optional[str] = None,
        is_active: bool = True,
        seen_at: Optional[str] = None,
    ) -> None:
        """Insert-or-refresh one registry row (ADR-008 "dataset registry").

        ``first_seen_at`` is written once; a later registration only advances
        ``last_seen_at`` and the physical facts, so the registry always
        describes the latest known file while run history stays attached to the
        ids that produced it."""
        with self._write_tx() as conn:
            self._upsert_dataset(
                conn,
                dataset_id=dataset_id,
                name=name,
                connector=connector,
                source_ref=source_ref,
                source_kind=source_kind,
                content_hash=content_hash,
                size_bytes=size_bytes,
                modified_at=modified_at,
                grain=grain,
                is_active=is_active,
                seen_at=seen_at,
            )

    @staticmethod
    def _upsert_dataset(
        conn: sqlite3.Connection,
        *,
        dataset_id: str,
        name: str,
        connector: str,
        source_ref: str,
        source_kind: str,
        content_hash: Optional[str],
        size_bytes: Optional[int],
        modified_at: Optional[str],
        grain: Optional[str],
        is_active: bool,
        seen_at: Optional[str],
    ) -> None:
        when = seen_at or _now_z()
        conn.execute(
            """
            INSERT INTO datasets (dataset_id, name, source_kind, connector, source_ref,
                                  content_hash, size_bytes, modified_at, grain, is_active,
                                  first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dataset_id) DO UPDATE SET
                name = excluded.name,
                source_kind = excluded.source_kind,
                connector = excluded.connector,
                source_ref = excluded.source_ref,
                content_hash = COALESCE(excluded.content_hash, datasets.content_hash),
                size_bytes = COALESCE(excluded.size_bytes, datasets.size_bytes),
                modified_at = COALESCE(excluded.modified_at, datasets.modified_at),
                grain = COALESCE(excluded.grain, datasets.grain),
                is_active = excluded.is_active,
                last_seen_at = MAX(datasets.last_seen_at, excluded.last_seen_at)
            """,
            (
                dataset_id, name, source_kind, connector, source_ref,
                content_hash, size_bytes, modified_at, grain, 1 if is_active else 0,
                when, when,
            ),
        )

    def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        return self._query_one("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,))

    def list_datasets(self, *, include_inactive: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM datasets"
        if not include_inactive:
            sql += " WHERE is_active = 1"
        return self._query(sql + " ORDER BY last_seen_at DESC, dataset_id")

    def set_dataset_active(self, dataset_id: str, active: bool) -> None:
        """Soft hide/restore a dataset. Actual removal of a dataset's history
        and artifacts is an explicit user action (SECURITY §4), never a side
        effect of hiding it."""
        with self._write_tx() as conn:
            cur = conn.execute(
                "UPDATE datasets SET is_active = ?, last_seen_at = ? WHERE dataset_id = ?",
                (1 if active else 0, _now_z(), dataset_id),
            )
            if cur.rowcount == 0:
                raise MetadataStoreError(f"unknown dataset_id {dataset_id!r}")

    # -- config versions ----------------------------------------------------

    def save_config_version(
        self,
        dataset_id: str,
        config: DatasetConfig,
        *,
        config_hash: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Persist one §9 config version (ADR-008 "config versions").

        Validated against ``dataset_config.schema.json`` first — invalid config
        is rejected before anything is stored, mirroring the engine's rejection
        before profiling (AGENTS §5). Returns ``(config_version, config_hash)``,
        the key a run row references; re-saving an identical version is a no-op."""
        document = _model_doc(config)
        _validate("dataset_config.schema.json", document, context="dataset config")
        digest = config_hash or config_document_hash(config)
        version = str(config.config_version)
        with self._write_tx() as conn:
            if conn.execute(
                "SELECT 1 FROM datasets WHERE dataset_id = ?", (dataset_id,)
            ).fetchone() is None:
                raise MetadataStoreError(
                    f"dataset {dataset_id!r} is not registered; call register_dataset() first"
                )
            conn.execute(
                """
                INSERT OR IGNORE INTO dataset_config_versions
                    (dataset_id, config_version, config_hash, profile_mode, document_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id, version, digest,
                    str(config.profiling.mode), _dump_json(document), created_at or _now_z(),
                ),
            )
        return version, digest

    def get_config_version(
        self, dataset_id: str, config_version: str, config_hash: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM dataset_config_versions WHERE dataset_id = ? AND config_version = ?"
        params: List[Any] = [dataset_id, str(config_version)]
        if config_hash is not None:
            sql += " AND config_hash = ?"
            params.append(config_hash)
        return self._query_one(sql + " LIMIT 1", params)

    def load_config_version(
        self, dataset_id: str, config_version: str, config_hash: Optional[str] = None
    ) -> Optional[DatasetConfig]:
        """Rebuild the §9 model from the stored document (a config is *input*,
        so it round-trips exactly — the reproducibility anchor)."""
        row = self.get_config_version(dataset_id, config_version, config_hash)
        if row is None:
            return None
        return DatasetConfig.model_validate(json.loads(row["document_json"]))

    # -- jobs ---------------------------------------------------------------

    def create_job(
        self,
        job_id: str,
        dataset_id: str,
        *,
        command: str = "profile.start",
        state: str = "QUEUED",
        created_at: Optional[str] = None,
    ) -> None:
        """Register a job (§6.2 ``QUEUED`` at registration). Transition rules
        belong to the job manager (M5-T2); the store persists them so status is
        observable and recoverable outside the process that started it."""
        allowed = OPEN_JOB_STATES + TERMINAL_JOB_STATES
        if state not in allowed:
            raise MetadataStoreError(f"unknown job state {state!r}; expected one of {allowed}")
        when = created_at or _now_z()
        with self._write_tx() as conn:
            conn.execute(
                """
                INSERT INTO jobs (job_id, dataset_id, command, state,
                                  progress_completed, progress_total, current_step,
                                  error_code, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, 0, NULL, NULL, NULL, ?, ?)
                """,
                (job_id, dataset_id, command, state, when, when),
            )

    def save_job_status(self, status: JobStatus, *, updated_at: Optional[str] = None) -> None:
        """Persist the §5.3 polled-status object (state + progress + error code).

        ``result`` is the *bounded* status payload, never a result document —
        large output stays in the artifact store (ADR-008, §5.4 payload rules)."""
        document = _model_doc(status)
        _validate("job_status.schema.json", document, context="job status")
        with self._write_tx() as conn:
            cur = conn.execute(
                """
                UPDATE jobs SET state = ?, progress_completed = ?, progress_total = ?,
                                current_step = ?, error_code = ?, result_json = ?,
                                updated_at = ?
                WHERE job_id = ?
                """,
                (
                    status.status,
                    status.progress.completed_steps,
                    status.progress.total_steps,
                    status.progress.current_step,
                    status.error_code,
                    _opt_json(status.result),
                    updated_at or _now_z(),
                    status.job_id,
                ),
            )
            if cur.rowcount == 0:
                raise MetadataStoreError(
                    f"unknown job_id {status.job_id!r}", code=ErrorCode.JOB_NOT_FOUND
                )

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._query_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))

    def load_job_status(self, job_id: str) -> Optional[JobStatus]:
        row = self.get_job(job_id)
        if row is None:
            return None
        return _build_model(
            JobStatus,
            job_id=row["job_id"],
            status=row["state"],
            progress=_build_model(
                JobProgress,
                completed_steps=row["progress_completed"],
                total_steps=row["progress_total"],
                current_step=row["current_step"],
            ),
            error_code=row["error_code"],
            result=_load_json(row["result_json"]),
        )

    def unfinished_jobs(self) -> List[Dict[str, Any]]:
        """Jobs left in a non-terminal state — the startup query that lets the
        host surface/recover what an interrupted worker was doing (§6.3
        "recovery on app restart", MVP acceptance line)."""
        placeholders = ",".join("?" for _ in OPEN_JOB_STATES)
        return self._query(
            f"SELECT * FROM jobs WHERE state IN ({placeholders}) ORDER BY updated_at",
            OPEN_JOB_STATES,
        )

    # -- run write ----------------------------------------------------------

    def save_run(
        self,
        bundle: "ProfileBundle",
        *,
        config: Optional[DatasetConfig] = None,
        artifact_refs: Optional[Mapping[str, str]] = None,
        now: Optional[str] = None,
    ) -> str:
        """Persist one completed profiling run and everything it produced, in a
        **single short transaction** — so a run can never exist without its
        checks/findings/artifact references (partial history is the corruption
        ADR-006's crash-isolation criterion is about).

        ``config`` additionally stores the §9 document under the run's own
        ``config_hash``, guaranteeing the two agree. ``artifact_refs``
        extends/overrides ``run.result_refs`` for files the caller wrote itself
        (the CLI's ``--out``, an M4 report); each becomes a ``run_artifacts``
        reference row — "large results live as files, referenced by rows"."""
        run = bundle.run
        run_doc = _model_doc(run)
        _validate("profiling_run.schema.json", run_doc, context="profiling run")
        refs: Dict[str, str] = dict(run.result_refs or {})
        refs.update(dict(artifact_refs or {}))
        created = now or _now_z()

        with self._write_tx() as conn:
            snapshot = _model_doc(run.dataset.source_snapshot)
            self._upsert_dataset(
                conn,
                dataset_id=run.dataset.dataset_id,
                name=run.dataset.dataset_id,
                connector=run.dataset.connector,
                source_ref=run.dataset.source_ref,
                source_kind=run.dataset.source_kind,
                content_hash=run.dataset.source_snapshot.content_hash,
                size_bytes=run.dataset.source_snapshot.size_bytes,
                modified_at=run.dataset.source_snapshot.modified_at,
                grain=bundle.profile.grain,
                is_active=True,
                seen_at=created,
            )
            if config is not None:
                document = _model_doc(config)
                _validate("dataset_config.schema.json", document, context="dataset config")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO dataset_config_versions
                        (dataset_id, config_version, config_hash, profile_mode, document_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.dataset.dataset_id, run.config.config_version, run.config.config_hash,
                        run.config.profile_mode, _dump_json(document), created,
                    ),
                )
            if run.job_id and conn.execute(
                "SELECT 1 FROM jobs WHERE job_id = ?", (run.job_id,)
            ).fetchone() is None:
                # a dangling job_id is a caller bug; failing loudly beats an orphan row
                raise MetadataStoreError(
                    f"run {run.run_id!r} references unknown job_id {run.job_id!r}",
                    code=ErrorCode.JOB_NOT_FOUND,
                )

            sampling = run.sampling
            conn.execute(
                """
                INSERT INTO runs (run_id, job_id, dataset_id, schema_version, status,
                                  source_kind, connector, source_ref, source_snapshot_json,
                                  config_version, config_hash, profile_mode,
                                  engine_version, duckdb_version, python_version,
                                  started_at, finished_at, duration_ms,
                                  sampling_enabled, sampling_strategy, sample_size, sample_seed,
                                  present_keys_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id, run.job_id, run.dataset.dataset_id, run.schema_version, run.status,
                    run.dataset.source_kind, run.dataset.connector, run.dataset.source_ref,
                    _dump_json(snapshot),
                    run.config.config_version, run.config.config_hash, run.config.profile_mode,
                    run.engine.engine_version, run.engine.duckdb_version, run.engine.python_version,
                    run.timing.started_at, run.timing.finished_at, run.timing.duration_ms,
                    # three states, not two: an absent §2 sampling block stays NULL,
                    # a declared-off block stays 0 (never reported as "no record")
                    None if sampling is None else (1 if sampling.enabled else 0),
                    sampling.strategy if sampling else None,
                    sampling.sample_size if sampling else None,
                    sampling.seed if sampling else None,
                    _present_keys(run_doc),
                ),
            )
            self._insert_profile_summary(conn, bundle)
            self._insert_columns(conn, bundle)
            self._insert_column_metrics(conn, bundle)
            self._insert_checks(conn, bundle)
            self._insert_findings(conn, bundle)
            self._insert_recommendations(conn, bundle)
            self._insert_run_errors(conn, run)
            self._insert_run_warnings(conn, run)
            self._insert_artifact_refs(conn, run.run_id, refs, created)
        return run.run_id

    @staticmethod
    def _insert_profile_summary(conn: sqlite3.Connection, bundle: "ProfileBundle") -> None:
        """§3.1 scalars only: row/column counts WITH their method label, declared
        grain, key candidates, temporal window. An absent candidate list stays
        absent (never an empty list invented to fill the column)."""
        profile = bundle.profile
        row_count = profile.row_count
        temporal = profile.temporal_coverage
        conn.execute(
            """
            INSERT INTO dataset_profiles (run_id, schema_version, row_count, row_count_method,
                                         row_count_reason, column_count, grain,
                                         primary_key_candidates_json, temporal_column,
                                         temporal_min, temporal_max, temporal_provenance_json,
                                         source_metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile.run_id, profile.schema_version,
                row_count.value if row_count is not None else None,
                row_count.provenance.method if row_count is not None else None,
                row_count.provenance.reason if row_count is not None else None,
                profile.column_count,
                profile.grain,
                _opt_json(profile.primary_key_candidates),
                temporal.column if temporal is not None else None,
                temporal.min if temporal is not None else None,
                temporal.max if temporal is not None else None,
                _opt_json(temporal.provenance.model_dump(mode="json")) if temporal is not None else None,
                _opt_json(profile.source_metadata),
            ),
        )

    @staticmethod
    def _insert_columns(conn: sqlite3.Connection, bundle: "ProfileBundle") -> None:
        # Identity + the physical/logical/semantic split only (MVP acceptance
        # requires that distinction to be visible); metric bodies are artifact
        # content, normalised metrics are M3-T3.
        conn.executemany(
            """
            INSERT INTO run_columns (run_id, ordinal, column_name, physical_type,
                                     logical_type, semantic_type, unit_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    bundle.profile.run_id, column.ordinal, column.column,
                    column.physical_type, column.logical_type, column.semantic_type,
                    _opt_json(column.unit.model_dump(mode="json")) if column.unit is not None else None,
                )
                for column in bundle.profile.columns
            ],
        )

    @staticmethod
    def _insert_column_metrics(conn: sqlite3.Connection, bundle: "ProfileBundle") -> None:
        """Promote each §3.2 MetricValue leaf to one addressed row (M3-T3).

        The full column document still lives in the ``profile_json`` artifact
        (ADR-008); these rows are the cheap-diff index, so a value here is a
        *projection* of the artifact, never an independent edit. Provenance is
        stored beside every value exactly as §1 requires — an absent ``value``
        (the ``not_computed`` case) stays absent rather than becoming a silent
        null, and a sampled metric keeps its seed inside ``method_params``."""
        rows = [
            (
                bundle.profile.run_id,
                column.column,
                metric_name,
                _field_json(metric, "value"),
                metric.provenance.method,
                _field_json(metric.provenance, "method_params"),
                metric.provenance.reason,
                metric.denominator,
            )
            for column in bundle.profile.columns
            for metric_name, metric in _iter_column_metrics(column)
        ]
        conn.executemany(
            """
            INSERT INTO column_metric_values (run_id, column_name, metric_name,
                                              value_json, method, method_params_json,
                                              reason, denominator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    @staticmethod
    def _insert_checks(conn: sqlite3.Connection, bundle: "ProfileBundle") -> None:
        for check in bundle.checks:
            check_doc = _model_doc(check)
            _validate(
                "quality_check_result.schema.json", check_doc, context="quality check"
            )
            observed = check.observed
            threshold = check.threshold
            evidence = check.evidence
            error = check.error
            observed_value_json = _field_json(observed, "value") if observed is not None else None
            conn.execute(
                """
                INSERT INTO quality_checks (run_id, rule_id, schema_version, rule_version,
                                            dimension, status, severity, column_name,
                                            observed_value_json, observed_method,
                                            observed_method_params_json, observed_reason,
                                            observed_denominator, threshold_operator,
                                            threshold_value_json, threshold_upper_json,
                                            failing_record_count, error_code, error_message,
                                            executed_at, evidence_denominator, evidence_baseline_ref,
                                            present_keys_json, evidence_present_keys_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    check.run_id, check.rule_id, check.schema_version, check.rule_version,
                    check.dimension, check.status, check.severity, check.column,
                    observed_value_json,
                    observed.provenance.method if observed is not None else None,
                    _field_json(observed.provenance, "method_params") if observed is not None else None,
                    observed.provenance.reason if observed is not None else None,
                    observed.denominator if observed is not None else None,
                    threshold.operator if threshold is not None else None,
                    _field_json(threshold, "value") if threshold is not None else None,
                    _field_json(threshold, "upper") if threshold is not None else None,
                    check.failing_record_count,
                    error.code if error is not None else None,
                    error.message if error is not None else None,
                    check.executed_at,
                    evidence.denominator if evidence is not None else None,
                    evidence.baseline_ref if evidence is not None else None,
                    _present_keys(check_doc),
                    _present_keys(_model_doc(evidence)) if evidence is not None else None,
                ),
            )
            conn.executemany(
                "INSERT INTO check_evidence_refs (run_id, rule_id, seq, ref) VALUES (?, ?, ?, ?)",
                [
                    (check.run_id, check.rule_id, seq, ref)
                    for seq, ref in enumerate((evidence.sample_row_refs if evidence else None) or [])
                ],
            )

    @staticmethod
    def _insert_findings(conn: sqlite3.Connection, bundle: "ProfileBundle") -> None:
        for finding in bundle.findings:
            finding_doc = _model_doc(finding)
            _validate("finding.schema.json", finding_doc, context="finding")
            scope = finding.scope
            workflow = finding.workflow
            conn.execute(
                """
                INSERT INTO findings (run_id, finding_id, schema_version, fingerprint, title,
                                      summary, category, severity, confidence, scope_column,
                                      affected_records, scope_period_json, interpretation,
                                      workflow_status, workflow_json, present_keys_json,
                                      scope_present_keys_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding.run_id, finding.finding_id, finding.schema_version,
                    finding.fingerprint, finding.title, finding.summary, finding.category,
                    finding.severity, finding.confidence, scope.column, scope.affected_records,
                    _field_json(scope, "period"),
                    finding.interpretation,
                    workflow.status if workflow is not None else None,
                    _field_json(finding, "workflow"),
                    _present_keys(finding_doc),
                    _present_keys(_model_doc(scope)) if scope is not None else None,
                ),
            )
            rid, fid = finding.run_id, finding.finding_id
            conn.executemany(
                "INSERT INTO finding_evidence_refs (run_id, finding_id, seq, ref) VALUES (?, ?, ?, ?)",
                [(rid, fid, seq, ref) for seq, ref in enumerate(finding.evidence_refs)],
            )
            conn.executemany(
                """
                INSERT INTO finding_facts (run_id, finding_id, seq, metric_ref, value_json,
                                           method, method_params_json, reason, present_keys_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        rid, fid, seq, fact.metric_ref,
                        _field_json(fact, "value"),
                        fact.provenance.method,
                        _opt_json(fact.provenance.method_params),
                        fact.provenance.reason,
                        _present_keys(_model_doc(fact)),
                    )
                    for seq, fact in enumerate(finding.facts)
                ],
            )
            conn.executemany(
                "INSERT INTO finding_hypotheses (run_id, finding_id, seq, text, status, "
                "suggested_probe, present_keys_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        rid, fid, seq, hyp.text, hyp.status, hyp.suggested_probe,
                        _present_keys(_model_doc(hyp)),
                    )
                    for seq, hyp in enumerate(finding.hypotheses)
                ],
            )
            conn.executemany(
                "INSERT INTO finding_related_refs (run_id, finding_id, seq, related_finding_id) "
                "VALUES (?, ?, ?, ?)",
                [
                    (rid, fid, seq, related)
                    for seq, related in enumerate(finding.related_finding_ids or [])
                ],
            )
            conn.executemany(
                "INSERT INTO finding_recommendation_refs (run_id, finding_id, seq, recommendation_id) "
                "VALUES (?, ?, ?, ?)",
                [
                    (rid, fid, seq, rec_id)
                    for seq, rec_id in enumerate(finding.recommendation_ids or [])
                ],
            )

    @staticmethod
    def _insert_recommendations(conn: sqlite3.Connection, bundle: "ProfileBundle") -> None:
        run_id = bundle.run.run_id
        for rec in bundle.recommendations:
            rec_doc = _model_doc(rec)
            _validate(
                "recommendation.schema.json", rec_doc, context="recommendation"
            )
            conn.execute(
                """
                INSERT INTO recommendations (run_id, recommendation_id, schema_version, finding_id,
                                             action, rationale, reason_type, target_improvement,
                                             status, present_keys_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, rec.recommendation_id, rec.schema_version, rec.finding_id,
                    rec.action, rec.rationale, rec.reason_type, rec.target_improvement,
                    rec.status, _present_keys(rec_doc),
                ),
            )
            conn.executemany(
                "INSERT INTO recommendation_evidence_refs "
                "(run_id, recommendation_id, seq, ref) VALUES (?, ?, ?, ?)",
                [
                    (run_id, rec.recommendation_id, seq, ref)
                    for seq, ref in enumerate(rec.evidence_refs)
                ],
            )
            conn.executemany(
                "INSERT INTO recommendation_do_not "
                "(run_id, recommendation_id, seq, text) VALUES (?, ?, ?, ?)",
                [
                    (run_id, rec.recommendation_id, seq, text)
                    for seq, text in enumerate(rec.do_not or [])
                ],
            )

    @staticmethod
    def _insert_run_errors(conn: sqlite3.Connection, run: ProfilingRun) -> None:
        """§6.4: a per-task failure is a row here, not a demoted run."""
        conn.executemany(
            "INSERT INTO run_errors (run_id, seq, code, task, message) VALUES (?, ?, ?, ?, ?)",
            [
                (run.run_id, seq, entry.code, entry.task, entry.message)
                for seq, entry in enumerate(run.error_log or [])
            ],
        )

    @staticmethod
    def _insert_run_warnings(conn: sqlite3.Connection, run: ProfilingRun) -> None:
        conn.executemany(
            "INSERT INTO run_warnings (run_id, seq, message) VALUES (?, ?, ?)",
            [(run.run_id, seq, message) for seq, message in enumerate(run.warnings or [])],
        )

    @staticmethod
    def _insert_artifact_refs(
        conn: sqlite3.Connection, run_id: str, refs: Mapping[str, str], created_at: str
    ) -> None:
        conn.executemany(
            "INSERT OR REPLACE INTO run_artifacts (run_id, kind, ref_path, created_at) "
            "VALUES (?, ?, ?, ?)",
            [(run_id, kind, str(path), created_at) for kind, path in sorted(refs.items())],
        )

    # -- run reads ----------------------------------------------------------

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))

    def list_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        return self._query(
            "SELECT kind, ref_path, created_at FROM run_artifacts WHERE run_id = ? "
            "ORDER BY kind, ref_path",
            (run_id,),
        )

    def artifact_ref(self, run_id: str, kind: str) -> Optional[str]:
        row = self._query_one(
            "SELECT ref_path FROM run_artifacts WHERE run_id = ? AND kind = ?", (run_id, kind)
        )
        return row["ref_path"] if row else None

    def all_artifact_refs(self) -> List[Dict[str, Any]]:
        """Every artifact reference in the store, across all runs.

        Read-only join point for the artifact **file** store's orphan sweep
        (M3-T2): the row set is the authority for which files are live, so a
        file with no row here is one a crashed/aborted run left behind. Kept
        deliberately narrow (no blob, no restructure of ``ref_path``) — the
        reference shape is still ``store-relative`` exactly as written."""
        return self._query(
            "SELECT run_id, kind, ref_path FROM run_artifacts ORDER BY run_id, kind, ref_path"
        )

    def load_profiling_run(self, run_id: str) -> Optional[ProfilingRun]:
        """Rebuild the §2 record from its rows and re-validate it: what was
        written is what comes back, with no field silently dropped."""
        row = self.get_run(run_id)
        if row is None:
            return None
        snapshot_doc = json.loads(row["source_snapshot_json"])
        sampling: Optional[RunSampling] = None
        if row["sampling_enabled"] is not None:
            # NULL == the run carried no sampling block at all; 0 == a declared
            # off record, which is a different statement and stays one.
            if row["sampling_enabled"]:
                sampling = RunSampling(
                    enabled=True,
                    strategy=row["sampling_strategy"],
                    sample_size=row["sample_size"],
                    seed=row["sample_seed"],
                )
            else:
                sampling = _build_model(
                    RunSampling,
                    enabled=False,
                    strategy=row["sampling_strategy"],
                    sample_size=row["sample_size"],
                    seed=row["sample_seed"],
                )
        errors = self._query(
            "SELECT code, task, message FROM run_errors WHERE run_id = ? ORDER BY seq", (run_id,)
        )
        warnings = self._query(
            "SELECT message FROM run_warnings WHERE run_id = ? ORDER BY seq", (run_id,)
        )
        refs = {a["kind"]: a["ref_path"] for a in self.list_artifacts(run_id)}
        kwargs: Dict[str, Any] = dict(
            schema_version=row["schema_version"],
            run_id=row["run_id"],
            job_id=row["job_id"],
            dataset=RunDataset(
                dataset_id=row["dataset_id"],
                source_kind=row["source_kind"],
                connector=row["connector"],
                source_ref=row["source_ref"],
                source_snapshot=SourceSnapshot.model_validate(snapshot_doc),
            ),
            config=_build_model(
                RunConfig,
                config_version=row["config_version"],
                config_hash=row["config_hash"],
                profile_mode=row["profile_mode"],
            ),
            engine=_build_model(
                RunEngine,
                engine_version=row["engine_version"],
                duckdb_version=row["duckdb_version"],
                python_version=row["python_version"],
            ),
            timing=_build_model(
                RunTiming,
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                duration_ms=row["duration_ms"],
            ),
            sampling=sampling,
            status=row["status"],
            result_refs=refs,
            error_log=[
                _build_model(RunErrorEntry, code=e["code"], task=e["task"], message=e["message"])
                for e in errors
            ],
            warnings=[w["message"] for w in warnings],
        )
        run = ProfilingRun.model_validate(_retain_present(kwargs, row["present_keys_json"]))
        _validate("profiling_run.schema.json", _model_doc(run), context="stored profiling run")
        return run

    def profile_summary(self, run_id: str) -> Optional[Dict[str, Any]]:
        """§3.1 scalar rollup as stored. Column metric bodies are not here by
        design — read the ``profile_json`` artifact, or M3-T3 for metrics."""
        row = self._query_one("SELECT * FROM dataset_profiles WHERE run_id = ?", (run_id,))
        if row is None:
            return None
        temporal = None
        if row["temporal_column"] is not None:
            temporal = {
                "column": row["temporal_column"],
                "min": row["temporal_min"],
                "max": row["temporal_max"],
                "provenance": _load_json(row["temporal_provenance_json"]),
            }
        return {
            "run_id": row["run_id"],
            "schema_version": row["schema_version"],
            "row_count": row["row_count"],
            "row_count_method": row["row_count_method"],
            "row_count_reason": row["row_count_reason"],
            "column_count": row["column_count"],
            "grain": row["grain"],
            "primary_key_candidates": _load_json(row["primary_key_candidates_json"]),
            "temporal_coverage": temporal,
            "source_metadata": _load_json(row["source_metadata_json"]),
        }

    def column_inventory(self, run_id: str) -> List[Dict[str, Any]]:
        return self._query(
            "SELECT ordinal, column_name, physical_type, logical_type, semantic_type, unit_json "
            "FROM run_columns WHERE run_id = ? ORDER BY ordinal",
            (run_id,),
        )

    # -- normalised metric values (M3-T3, cheap run-history diffs) ----------

    @staticmethod
    def _row_to_metric_value(row: Dict[str, Any]) -> MetricValue:
        """Rebuild one §3.2 ``MetricValue`` from its normalised row, reusing the
        exact provenance/absence logic the check-observation reader uses so a
        diff cell round-trips identically to the artifact it projects: an absent
        ``value`` stays absent (``not_computed``), an explicit null survives."""
        provenance = _build_model(
            Provenance,
            method=row["method"],
            method_params=_load_json(row["method_params_json"]),
            reason=row["reason"],
        )
        kwargs: Dict[str, Any] = {"provenance": provenance}
        if row["denominator"] is not None:
            kwargs["denominator"] = row["denominator"]
        _apply_json_field(kwargs, row["value_json"], "value")
        return MetricValue.model_validate(kwargs)

    def column_metrics(
        self,
        run_id: str,
        *,
        column_name: Optional[str] = None,
        metric_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Normalised metric rows for a run, optionally narrowed to one column
        and/or one §3.2 metric. The full-inventory read a diff screen pages
        through; values are returned as stored (JSON-typed) columns."""
        sql = (
            "SELECT column_name, metric_name, value_json, method, method_params_json, "
            "       reason, denominator "
            "FROM column_metric_values WHERE run_id = ?"
        )
        params: List[Any] = [run_id]
        if column_name is not None:
            sql += " AND column_name = ?"
            params.append(column_name)
        if metric_name is not None:
            sql += " AND metric_name = ?"
            params.append(metric_name)
        sql += " ORDER BY column_name, metric_name"
        return self._query(sql, params)

    def metric_value(
        self, run_id: str, column_name: str, metric_name: str
    ) -> Optional[MetricValue]:
        """Read a single numeric cell without opening the artifact — the point
        of M3-T3. Returns the reconstructed ``MetricValue`` (value + ADR-015
        provenance), or ``None`` when that cell was never reported for the run
        (absent, not a zero or a null)."""
        row = self._query_one(
            "SELECT value_json, method, method_params_json, reason, denominator "
            "FROM column_metric_values WHERE run_id = ? AND column_name = ? "
            "AND metric_name = ?",
            (run_id, column_name, metric_name),
        )
        return self._row_to_metric_value(row) if row is not None else None

    def metric_history(
        self,
        dataset_id: str,
        column_name: str,
        metric_name: str,
        *,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """One metric cell across every run of a dataset, newest first — the
        cross-run read M6's diff/baseline screen is built on. Joining through
        ``runs`` keeps it to SUCCEEDED-or-other terminal history in time order
        and carries the provenance so a method change is visible as *not* a
        data change (ADR-015 consequence)."""
        sql = (
            "SELECT m.run_id, r.started_at, r.status, r.config_hash, r.engine_version, "
            "       m.value_json, m.method, m.method_params_json, m.reason, m.denominator "
            "FROM column_metric_values m JOIN runs r ON r.run_id = m.run_id "
            "WHERE r.dataset_id = ? AND m.column_name = ? AND m.metric_name = ? "
            "ORDER BY r.started_at DESC, m.run_id"
        )
        params: List[Any] = [dataset_id, column_name, metric_name]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return self._query(sql, params)

    def list_checks(
        self,
        run_id: str,
        *,
        status: Optional[str] = None,
        dimension: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Paged check fetch — the bridge never ships an unbounded set (§5.4)."""
        sql = "SELECT * FROM quality_checks WHERE run_id = ?"
        params: List[Any] = [run_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if dimension:
            sql += " AND dimension = ?"
            params.append(dimension)
        sql += " ORDER BY rule_id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        return self._query(sql, params)

    def count_checks(
        self,
        run_id: str,
        *,
        status: Optional[str] = None,
        dimension: Optional[str] = None,
    ) -> int:
        """Total rows the identically-filtered :meth:`list_checks` would return
        (no LIMIT/OFFSET) — the page-count a paged bridge response carries so
        the UI knows when to stop paging (§5.4)."""
        sql = "SELECT COUNT(*) AS n FROM quality_checks WHERE run_id = ?"
        params: List[Any] = [run_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if dimension:
            sql += " AND dimension = ?"
            params.append(dimension)
        row = self._query_one(sql, params)
        return int(row["n"]) if row else 0

    def load_check_results(self, run_id: str) -> List[QualityCheckResult]:
        """Rebuild §4.1 objects. ``ERROR`` comes back with its error object and
        a PASS/FAIL/WARN without, because the model itself refuses any other
        pairing (FAIL ≠ ERROR is enforced on the way out too, not just in)."""
        out: List[QualityCheckResult] = []
        for row in self.list_checks(run_id):
            refs = self._query(
                "SELECT ref FROM check_evidence_refs WHERE run_id = ? AND rule_id = ? ORDER BY seq",
                (row["run_id"], row["rule_id"]),
            )
            observed: Optional[MetricValue] = None
            if row["observed_method"]:
                provenance = _build_model(
                    Provenance,
                    method=row["observed_method"],
                    method_params=_load_json(row["observed_method_params_json"]),
                    reason=row["observed_reason"],
                )
                observed_kwargs: Dict[str, Any] = {"provenance": provenance}
                if row["observed_denominator"] is not None:
                    # §1: `denominator` is not a nullable key — an absent one is
                    # omitted, never sent back as null
                    observed_kwargs["denominator"] = row["observed_denominator"]
                _apply_json_field(observed_kwargs, row["observed_value_json"], "value")
                observed = MetricValue.model_validate(observed_kwargs)
            threshold = None
            if row["threshold_operator"]:
                threshold_kwargs: Dict[str, Any] = {"operator": row["threshold_operator"]}
                _apply_json_field(threshold_kwargs, row["threshold_value_json"], "value")
                _apply_json_field(threshold_kwargs, row["threshold_upper_json"], "upper")
                threshold = CheckThreshold.model_validate(threshold_kwargs)
            evidence = None
            if row["evidence_present_keys_json"] is not None:
                evidence_kwargs: Dict[str, Any] = {
                    "denominator": row["evidence_denominator"],
                    "sample_row_refs": [r["ref"] for r in refs],
                    "baseline_ref": row["evidence_baseline_ref"],
                }
                evidence = CheckEvidence.model_validate(
                    _retain_present(evidence_kwargs, row["evidence_present_keys_json"])
                )
            error = None
            if row["error_code"]:
                error = _build_model(
                    CheckError, code=row["error_code"], message=row["error_message"]
                )
            check_kwargs: Dict[str, Any] = dict(
                schema_version=row["schema_version"],
                run_id=row["run_id"],
                rule_id=row["rule_id"],
                rule_version=row["rule_version"],
                dimension=row["dimension"],
                status=row["status"],
                severity=row["severity"],
                column=row["column_name"],
                observed=observed,
                threshold=threshold,
                failing_record_count=row["failing_record_count"],
                evidence=evidence,
                error=error,
                executed_at=row["executed_at"],
            )
            check = QualityCheckResult.model_validate(
                _retain_present(check_kwargs, row["present_keys_json"])
            )
            _validate(
                "quality_check_result.schema.json", _model_doc(check), context="stored check"
            )
            out.append(check)
        return out

    def list_findings(
        self,
        run_id: str,
        *,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM findings WHERE run_id = ?"
        params: List[Any] = [run_id]
        if severity:
            sql += " AND severity = ?"
            params.append(severity)
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY finding_id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        return self._query(sql, params)

    def count_findings(
        self,
        run_id: str,
        *,
        severity: Optional[str] = None,
        category: Optional[str] = None,
    ) -> int:
        """Total rows the identically-filtered :meth:`list_findings` would
        return — see :meth:`count_checks` for why the paged fetch needs it."""
        sql = "SELECT COUNT(*) AS n FROM findings WHERE run_id = ?"
        params: List[Any] = [run_id]
        if severity:
            sql += " AND severity = ?"
            params.append(severity)
        if category:
            sql += " AND category = ?"
            params.append(category)
        row = self._query_one(sql, params)
        return int(row["n"]) if row else 0

    def load_findings(self, run_id: str) -> List[Finding]:
        """Rebuild §4.2 objects with facts / interpretation / hypotheses kept in
        their separate structures (CONTRACTS §7) and each fact's provenance
        label intact (ADR-015)."""
        out: List[Finding] = []
        for row in self.list_findings(run_id):
            rid, fid = row["run_id"], row["finding_id"]
            scope_kwargs: Dict[str, Any] = {
                "column": row["scope_column"],
                "affected_records": row["affected_records"],
            }
            _apply_json_field(scope_kwargs, row["scope_period_json"], "period")
            scope = FindingScope.model_validate(
                _retain_present(scope_kwargs, row["scope_present_keys_json"])
            )
            evidence = [
                r["ref"] for r in self._query(
                    "SELECT ref FROM finding_evidence_refs "
                    "WHERE run_id = ? AND finding_id = ? ORDER BY seq",
                    (rid, fid),
                )
            ]
            facts = []
            for f in self._query(
                "SELECT * FROM finding_facts WHERE run_id = ? AND finding_id = ? ORDER BY seq",
                (rid, fid),
            ):
                fact_kwargs: Dict[str, Any] = {
                    "metric_ref": f["metric_ref"],
                    "provenance": _build_model(
                        Provenance,
                        method=f["method"],
                        method_params=_load_json(f["method_params_json"]),
                        reason=f["reason"],
                    ),
                }
                _apply_json_field(fact_kwargs, f["value_json"], "value")
                facts.append(
                    FindingFact.model_validate(_retain_present(fact_kwargs, f["present_keys_json"]))
                )
            hypotheses = [
                FindingHypothesis.model_validate(
                    _retain_present(
                        {
                            "text": h["text"],
                            "status": h["status"],
                            "suggested_probe": h["suggested_probe"],
                        },
                        h["present_keys_json"],
                    )
                )
                for h in self._query(
                    "SELECT * FROM finding_hypotheses "
                    "WHERE run_id = ? AND finding_id = ? ORDER BY seq",
                    (rid, fid),
                )
            ]
            related = [
                r["related_finding_id"] for r in self._query(
                    "SELECT related_finding_id FROM finding_related_refs "
                    "WHERE run_id = ? AND finding_id = ? ORDER BY seq",
                    (rid, fid),
                )
            ]
            rec_ids = [
                r["recommendation_id"] for r in self._query(
                    "SELECT recommendation_id FROM finding_recommendation_refs "
                    "WHERE run_id = ? AND finding_id = ? ORDER BY seq",
                    (rid, fid),
                )
            ]
            finding_kwargs: Dict[str, Any] = dict(
                schema_version=row["schema_version"],
                finding_id=fid,
                fingerprint=row["fingerprint"],
                run_id=rid,
                title=row["title"],
                summary=row["summary"],
                category=row["category"],
                severity=row["severity"],
                confidence=row["confidence"],
                scope=scope,
                facts=facts,
                interpretation=row["interpretation"],
                hypotheses=hypotheses,
                evidence_refs=evidence,
                related_finding_ids=related,
                recommendation_ids=rec_ids,
            )
            _apply_json_field(finding_kwargs, row["workflow_json"], "workflow")
            finding = Finding.model_validate(
                _retain_present(finding_kwargs, row["present_keys_json"])
            )
            _validate("finding.schema.json", _model_doc(finding), context="stored finding")
            out.append(finding)
        return out

    def list_recommendations(
        self,
        run_id: str,
        *,
        finding_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM recommendations WHERE run_id = ?"
        params: List[Any] = [run_id]
        if finding_id:
            sql += " AND finding_id = ?"
            params.append(finding_id)
        sql += " ORDER BY recommendation_id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        return self._query(sql, params)

    def count_recommendations(
        self,
        run_id: str,
        *,
        finding_id: Optional[str] = None,
    ) -> int:
        """Total rows the identically-filtered :meth:`list_recommendations` would
        return — see :meth:`count_findings` for why the paged fetch needs it."""
        sql = "SELECT COUNT(*) AS n FROM recommendations WHERE run_id = ?"
        params: List[Any] = [run_id]
        if finding_id:
            sql += " AND finding_id = ?"
            params.append(finding_id)
        row = self._query_one(sql, params)
        return int(row["n"]) if row else 0

    def load_recommendations(self, run_id: str) -> List[Recommendation]:
        out: List[Recommendation] = []
        for row in self.list_recommendations(run_id):
            rid, rec_id = row["run_id"], row["recommendation_id"]
            evidence = [
                r["ref"] for r in self._query(
                    "SELECT ref FROM recommendation_evidence_refs "
                    "WHERE run_id = ? AND recommendation_id = ? ORDER BY seq",
                    (rid, rec_id),
                )
            ]
            do_not = [
                d["text"] for d in self._query(
                    "SELECT text FROM recommendation_do_not "
                    "WHERE run_id = ? AND recommendation_id = ? ORDER BY seq",
                    (rid, rec_id),
                )
            ]
            rec_kwargs: Dict[str, Any] = dict(
                schema_version=row["schema_version"],
                recommendation_id=rec_id,
                finding_id=row["finding_id"],
                action=row["action"],
                rationale=row["rationale"],
                reason_type=row["reason_type"],
                target_improvement=row["target_improvement"],
                do_not=do_not,
                evidence_refs=evidence,
                status=row["status"],
            )
            rec = Recommendation.model_validate(
                _retain_present(rec_kwargs, row["present_keys_json"])
            )
            _validate("recommendation.schema.json", _model_doc(rec), context="stored recommendation")
            out.append(rec)
        return out

    def list_runs(
        self,
        dataset_id: Optional[str] = None,
        *,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM runs"
        clauses: List[str] = []
        params: List[Any] = []
        if dataset_id:
            clauses.append("dataset_id = ?")
            params.append(dataset_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC, run_id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        return self._query(sql, params)

    def latest_run(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        rows = self.list_runs(dataset_id, limit=1)
        return rows[0] if rows else None

    def runs_by_fingerprint(
        self, fingerprint: str, *, dataset_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """History keyed by the ADR-016 fingerprint — the query Phase-4
        baseline/drift diffing is built on, available from the day runs
        persist. One fingerprint legitimately owns many runs, hence the
        per-run grouping."""
        sql = (
            "SELECT r.run_id, r.dataset_id, r.status, r.started_at, r.finished_at, "
            "       r.config_hash, r.engine_version, f.finding_id, f.severity "
            "FROM runs r JOIN findings f ON f.run_id = r.run_id "
            "WHERE f.fingerprint = ?"
        )
        params: List[Any] = [fingerprint]
        if dataset_id:
            sql += " AND r.dataset_id = ?"
            params.append(dataset_id)
        return self._query(sql + " ORDER BY r.started_at DESC, r.run_id", params)

    def check_status_counts(self, run_id: str) -> Dict[str, int]:
        """PASS/FAIL/WARN/ERROR tally — the cheapest proof the two failure
        semantics stayed separate, and the scorecard's raw material."""
        rows = self._query(
            "SELECT status, COUNT(*) AS n FROM quality_checks WHERE run_id = ? GROUP BY status",
            (run_id,),
        )
        return {str(r["status"]): int(r["n"]) for r in rows}

    def counts(self, run_id: str) -> Dict[str, int]:
        """Persisted row count per block for one run (integration assertion)."""
        out: Dict[str, int] = {}
        for table in (
            "dataset_profiles",
            "run_columns",
            "column_metric_values",
            "quality_checks",
            "findings",
            "recommendations",
            "run_errors",
            "run_warnings",
            "run_artifacts",
        ):
            row = self._query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE run_id = ?", (run_id,))
            out[table] = int(row["n"]) if row else 0
        return out


__all__ = [
    "DEFAULT_ARTIFACT_KIND",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "OPEN_JOB_STATES",
    "TERMINAL_JOB_STATES",
    "MetadataIntegrityError",
    "MetadataStore",
    "MetadataStoreError",
    "config_document_hash",
]
