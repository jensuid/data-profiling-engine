"""Connector base — file source → DuckDB relation (M2-T1).

Pluggable interface per ADR-011 ("connector interface must still be designed
as pluggable so Phase-2 adds without contract change") and ARCHITECTURE §3
(``connectors/`` = CSV, Parquet readers → DuckDB relations). MVP exposes only
``csv`` and ``parquet``; no Excel/SQL/API paths are reachable (AGENTS §2).

Hard rules honoured here (AGENTS §2):

- **Read-only against sources** — the relation is a view over
  ``read_csv``/``read_parquet`` table functions; nothing is ever written back.
- **Never load full data into Python RAM** — snapshot building streams a
  SHA-256 hash in 1 MiB chunks; column facts come from a ``LIMIT 0`` schema
  probe, never materialised rows (ADR-007).
- **Row-id policy (ENGINE §5.2)**: every connector binds a deterministic
  ``_row_id`` column (CSV: generated data-line number; Parquet:
  ``file_row_number`` = row-group+offset) so evidence ``sample_row_ids`` are
  reproducible for the same file + seed.
- **SourceSnapshot (CONTRACTS §2)**: ``path`` / ``size_bytes`` /
  ``modified_at`` / ``content_hash`` captured before any compute starts and
  carried verbatim into ``ProfilingRun.dataset``.
"""
from __future__ import annotations

import hashlib
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import duckdb

from data_profiling_engine import duckdb_session
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.schemas.models import SourceSnapshot

#: Contract-neutral connector names (CONTRACTS §2 ``dataset.connector``).
CSV = "csv"
PARQUET = "parquet"

#: Deterministic row-id column injected by every MVP connector (ENGINE §5.2).
ROW_ID_COLUMN = "_row_id"

#: Streaming hash / read chunk size (1 MiB; the file is never fully in RAM).
_CHUNK = 1024 * 1024


class ConnectorError(Exception):
    """Connector-layer failure carrying a registered CONTRACTS §8 code.

    ``message`` stays free of raw file content / sensitive values
    (SECURITY §5) — paths and driver diagnostics only.
    """

    code: ErrorCode

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DatasetReadFailed(ConnectorError):
    """§8 ``DATASET_READ_FAILED`` — source unreadable/missing/unparsable."""

    code = ErrorCode.DATASET_READ_FAILED


class SourceNotAllowed(ConnectorError):
    """§8 ``SOURCE_NOT_ALLOWED`` — path/scope outside the host allow-list."""

    code = ErrorCode.SOURCE_NOT_ALLOWED


@dataclass(frozen=True)
class RelationHandle:
    """Everything downstream modules (discovery/engine/quality) need about one
    registered source relation. Bound inside the caller's DuckDB connection;
    ``view_name`` is the identifier rule/metric SQL uses as its ``FROM`` target
    (ENGINE §5.2 compiles against "a relation")."""

    dataset_id: str
    connector: str
    view_name: str
    snapshot: SourceSnapshot
    columns: Tuple[Tuple[str, str], ...]  # (name, duckdb physical type), file order
    row_id_column: str
    extra_metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def column_names(self) -> Tuple[str, ...]:
        """Source columns only (``_row_id`` excluded — it is engine plumbing,
        not a profileable column; discovery iterates this tuple)."""
        return tuple(name for name, _ in self.columns)


def _rfc3339_z(ts: float) -> str:
    """Epoch seconds → ISO-8601 UTC ``...Z`` (CONTRACTS §1 timestamps)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(path: Path) -> str:
    """Stream sha256 over the file; returns ``sha256:<hex>`` (CONTRACTS §2)."""
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                h.update(chunk)
    except OSError as exc:
        raise DatasetReadFailed(f"source unreadable: {path} ({exc.strerror})") from exc
    return f"sha256:{h.hexdigest()}"


def _safe_view_name(dataset_id: str) -> str:
    """Quote-safe, collision-free DuckDB view name derived from any dataset id."""
    slug = re.sub(r"[^A-Za-z0-9_]", "_", dataset_id)[:40]
    digest = hashlib.sha256(dataset_id.encode("utf-8")).hexdigest()[:8]
    return f"src_{slug}_{digest}"


def _sql_str(value: str) -> str:
    """Escape a Python string for single-quoted SQL literal embedding (paths)."""
    return value.replace("'", "''")


class Connector(ABC):
    """One pluggable source adapter — the ADR-011 seam Phase-2 backends extend."""

    name: str = ""
    suffixes: Tuple[str, ...] = ()

    def __init__(self, dataset_id: str, path: "Path | str",
                 allowed_roots: "List[Path] | None" = None) -> None:
        self.dataset_id = dataset_id
        self.path = Path(path)
        self.allowed_roots = [Path(r).resolve() for r in (allowed_roots or [])]
        self.view_name = _safe_view_name(dataset_id)

    # -- validation ---------------------------------------------------------

    def validate(self) -> Path:
        """Resolve + allow-list + sanity-check the path *before* any compute
        (fail fast; AGENTS §7 path allow-list; ENGINE §4 gate 5).

        Raises :class:`SourceNotAllowed` for scope/format violations,
        :class:`DatasetReadFailed` for missing/unreadable/non-file paths.
        """
        if not self.path.exists():
            raise DatasetReadFailed(f"source missing: {self.path}")
        try:
            resolved = self.path.resolve(strict=True)
        except OSError as exc:
            raise DatasetReadFailed(f"source unresolvable: {self.path} ({exc})") from exc
        if not resolved.is_file():
            raise DatasetReadFailed(f"source is not a regular file: {resolved}")
        if self.allowed_roots and not any(
            resolved == root or root in resolved.parents for root in self.allowed_roots
        ):
            raise SourceNotAllowed(f"path outside allow-list: {resolved}")
        if resolved.suffix.lower() not in self.suffixes:
            raise SourceNotAllowed(
                f"{self.name} connector got suffix {resolved.suffix!r}; "
                f"expects one of {list(self.suffixes)}"
            )
        try:
            st = resolved.stat()
        except OSError as exc:
            raise DatasetReadFailed(f"source unreadable: {resolved} ({exc})") from exc
        if not os.access(resolved, os.R_OK):
            raise DatasetReadFailed(f"source not readable: {resolved}")
        return resolved

    # -- snapshot -----------------------------------------------------------

    def build_snapshot(self) -> SourceSnapshot:
        """CONTRACTS §2 ``source_snapshot``, captured pre-compute (audit tuple:
        content_hash + config_hash + engine_version + seed ⇒ reproducible run)."""
        resolved = self.validate()
        st = resolved.stat()
        return SourceSnapshot(
            path=str(resolved),
            size_bytes=st.st_size,
            modified_at=_rfc3339_z(st.st_mtime),
            content_hash=_content_hash(resolved),
            row_id_policy=f"{self.name}:{ROW_ID_COLUMN}",
        )

    # -- relation binding ---------------------------------------------------

    @abstractmethod
    def _table_function(self, path_literal: str) -> str:
        """DuckDB table-function call for the resolved file, e.g.
        ``read_csv('...')``. ``path_literal`` is an already-quoted SQL string."""

    @abstractmethod
    def _bare_table_function(self, path_literal: str) -> str:
        """Same scan WITHOUT reader-plumbing options — used to learn the true
        data columns of the file (collision checking)."""

    def _row_id_expr(self) -> str:
        """SQL expression (over the table function aliased ``src``) producing the
        deterministic per-row id; may reference reader-plumbing columns such as
        ``file_row_number`` that are NOT profileable source columns."""
        raise NotImplementedError

    def _plumbing_columns(self) -> Tuple[str, ...]:
        """Reader-added columns that are scan mechanics, not data (stripped
        from the schema before discovery ever sees them)."""
        return ()

    def _source_sql(self, resolved: Path, names: List[str]) -> str:
        """View body: explicit source columns in file order + ``_row_id``.
        ``row_number() OVER ()`` / ``file_row_number`` are evaluated at scan
        time — before any downstream filter — so ids stay reproducible
        (ENGINE §5.2); reader plumbing never leaks into the relation."""
        projection = ", ".join(f'"{n}"' for n in names)
        return (
            f"SELECT {projection}, {self._row_id_expr()} AS {ROW_ID_COLUMN} "
            f"FROM {self._table_function(chr(39) + _sql_str(str(resolved)) + chr(39))} AS src"
        )

    def _probe_schema(self, resolved: Path) -> Tuple[Tuple[Tuple[str, str], ...], Tuple[str, ...]]:
        """Two LIMIT-0 scans → ((name, physical type) source columns, raw data
        column names without reader plumbing). The bare scan establishes the
        true column list (collision checking); the plumbing scan proves the
        exact view SQL parses. Invalid sources fail here, before any
        profiling work, without materialising rows (ADR-007)."""
        con = duckdb_session.connect()
        lit = chr(39) + _sql_str(str(resolved)) + chr(39)
        try:
            bare = con.execute(f"SELECT * FROM {self._bare_table_function(lit)} LIMIT 0")
            # snapshot description NOW — a later execute() on the same
            # connection replaces the open result (gotcha)
            raw_desc = tuple(bare.description or ())
            con.execute(f"SELECT * FROM {self._table_function(lit)} LIMIT 0")
        except duckdb.Error as exc:
            raise DatasetReadFailed(
                f"source unparsable by DuckDB: {resolved} ({exc})"
            ) from exc
        cols = tuple((d[0], str(d[1]).upper()) for d in raw_desc)
        raw = tuple(d[0] for d in raw_desc)
        return cols, raw

    def register(self, con: duckdb.DuckDBPyConnection) -> RelationHandle:
        """Bind a read-only view ``view_name`` (source columns + ``_row_id``)
        in the given DuckDB connection and return its :class:`RelationHandle`."""
        resolved = self.validate()
        cols, raw_names = self._probe_schema(resolved)
        names = [name for name, _ in cols]
        if ROW_ID_COLUMN in raw_names or set(raw_names) & set(self._plumbing_columns()):
            # a data column literally named _row_id / file_row_number would
            # break or make ambiguous the row-id policy — reject early with a
            # registry code instead of a raw binder error.
            raise DatasetReadFailed(
                f"column name collides with {self.name} row-id/reader internals "
                f"{[ROW_ID_COLUMN, *self._plumbing_columns()]}: {resolved}"
            )
        try:
            con.execute(
                f'CREATE OR REPLACE VIEW "{self.view_name}" AS '
                f"{self._source_sql(resolved, names)}"
            )
        except duckdb.Error as exc:
            raise DatasetReadFailed(
                f"source unparsable by DuckDB: {resolved} ({exc})"
            ) from exc
        return RelationHandle(
            dataset_id=self.dataset_id,
            connector=self.name,
            view_name=self.view_name,
            snapshot=self.build_snapshot(),
            columns=cols,
            row_id_column=ROW_ID_COLUMN,
            extra_metadata=self._extra_metadata(resolved),
        )

    def _extra_metadata(self, resolved: Path) -> Dict[str, object]:
        """Connector facts feeding ``DatasetProfile.source_metadata``
        (CONTRACTS §3.1); override per format."""
        return {}
