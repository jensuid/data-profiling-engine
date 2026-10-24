"""Forward-only SQLite schema migrations for the metadata store (M3-T1).

ADR-008 makes a *migration mechanism* a mandatory consequence of using SQLite,
and SECURITY §6.5 fixes the policy this implements:

* migrations run **forward** on first launch of a new version;
* a **pre-migration backup** of the database is kept before anything changes;
* ``schema_version`` is documented (the CONTRACTS §1 contract version every
  persisted object carries) alongside the integer DDL version;
* **downgrade is refused**, not attempted — changes are additive where feasible
  and otherwise the documented path is restore-from-backup.

Design notes
------------
* One :class:`Migration` per DDL version: an idempotent, self-contained script
  applied inside a single transaction (``BEGIN IMMEDIATE``), so a half-applied
  migration can never leave the file stamped with the new version. A script
  that fails because a peer process (host and worker open the store too) got
  there first is recognised from the peer's stamp rather than reported as an
  error.
* The bookkeeping table ``schema_migrations`` records every applied version
  with its name, the contract ``schema_version`` it was written against and an
  UTC timestamp — the audit trail a support question needs ("which DDL built
  this user's file?").
* ``PRAGMA user_version`` is mirrored so a bare sqlite CLI shows the version
  without knowing our table name.
* ``MIGRATIONS`` is append-only: a released script is never edited in place,
  because existing user files already carry its stamp (add a new version).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

#: CONTRACTS §1 ``schema_version`` of the persisted contract objects this DDL
#: stores. Recorded on every migration stamp.
CONTRACT_SCHEMA_VERSION = "1.0"

#: Bookkeeping table name (created by the mechanism, not by a migration).
MIGRATION_TABLE = "schema_migrations"


class MigrationError(RuntimeError):
    """A migration could not be applied, or the file is not openable by this
    version of the engine (downgrade). Message carries no user data."""


@dataclass(frozen=True)
class Migration:
    """One DDL step: strictly increasing ``version`` + its SQL script."""

    version: int
    name: str
    sql: str


@dataclass(frozen=True)
class MigrationReport:
    """Outcome of :func:`apply_migrations` — what a caller/log line needs."""

    before: int
    after: int
    applied: Tuple[Tuple[int, str], ...] = field(default_factory=tuple)
    backup_path: Optional[Path] = None

    @property
    def changed(self) -> bool:
        return bool(self.applied)


# ---------------------------------------------------------------------------
# v1 — M3-T1 initial metadata schema
# ---------------------------------------------------------------------------
#
# Shape follows ADR-008 exactly: the store owns *metadata* — dataset registry,
# config versions, jobs, runs, findings metadata and artifact *references*.
# Large results (the full DatasetProfile/ColumnProfile document) live as files
# in the artifact store and are referenced from ``run_artifacts``; metric
# values are normalised per-column in M3-T3, so nothing here stores a blob.
#
# Natural keys everywhere (``(run_id, …)`` pairs) because CONTRACTS §1 scopes
# ids as "globally unique within a run": ``F-001`` repeats across runs, so any
# child table must carry ``run_id`` in its key, not just the child id.
_V1_SQL = """
-- Dataset registry: one row per source the app knows about. Identity is the
-- dataset_id the connector/contract uses; the snapshot columns describe the
-- *last seen* physical file (CONTRACTS §2 SourceSnapshot) so a UI list can
-- show what a row refers to without opening the source.
CREATE TABLE datasets (
    dataset_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    source_kind     TEXT NOT NULL,                    -- CONTRACTS §2 (file)
    connector       TEXT NOT NULL,                    -- csv | parquet (MVP)
    source_ref      TEXT NOT NULL,                    -- resolved path/uri
    content_hash    TEXT,                             -- sha256:<hex>
    size_bytes      INTEGER,
    modified_at     TEXT,
    grain           TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,       -- soft delete / hidden
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);

-- Config versions (ADR-008): the reproducibility anchor. The canonical §9
-- document is stored verbatim (it is engine *input*, bounded by the rule
-- count, never a result blob) so a run can be re-executed exactly.
CREATE TABLE dataset_config_versions (
    dataset_id      TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    config_version  TEXT NOT NULL,
    config_hash     TEXT NOT NULL,                    -- sha256:<hex>
    profile_mode    TEXT NOT NULL,
    document_json   TEXT NOT NULL,                    -- canonical §9 JSON
    created_at      TEXT NOT NULL,
    PRIMARY KEY (dataset_id, config_version, config_hash)
);

-- Job manager rows (CONTRACTS §6.2/§6.3): status + progress persisted so the
-- app can surface and recover jobs across a restart.
CREATE TABLE jobs (
    job_id              TEXT PRIMARY KEY,
    dataset_id          TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    command             TEXT NOT NULL,
    state               TEXT NOT NULL,                -- §6.2 including QUEUED/RUNNING
    progress_completed  INTEGER NOT NULL DEFAULT 0,
    progress_total      INTEGER NOT NULL DEFAULT 0,
    current_step        TEXT,
    error_code          TEXT,                         -- §8 registry code
    result_json         TEXT,                         -- bounded polled-status result
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_jobs_state ON jobs (state);
CREATE INDEX idx_jobs_dataset ON jobs (dataset_id, updated_at DESC);

-- Runs: the §2 record, fully normalised (every field the contract carries is
-- a column, so ``SELECT`` reconstructs the object without a JSON blob).
CREATE TABLE runs (
    run_id              TEXT PRIMARY KEY,
    job_id              TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
    dataset_id          TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    schema_version      TEXT NOT NULL,
    status              TEXT NOT NULL,                -- terminal §6.2 only
    source_kind         TEXT NOT NULL,
    connector           TEXT NOT NULL,
    source_ref          TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL,               -- §2 SourceSnapshot object
    config_version      TEXT NOT NULL,
    config_hash         TEXT NOT NULL,
    profile_mode        TEXT NOT NULL,
    engine_version      TEXT NOT NULL,
    duckdb_version      TEXT,
    python_version      TEXT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    duration_ms         INTEGER,
    sampling_enabled    INTEGER,                     -- NULL = no §2 sampling block; 0 = declared off; 1 = on
    sampling_strategy   TEXT,
    sample_size         INTEGER,
    sample_seed         INTEGER,
    -- SQL NULL cannot tell "the contract document omitted this key" from "the
    -- key was there and null", and CONTRACTS §1 makes that difference mean
    -- something, so each row records the key set it was written from.
    present_keys_json   TEXT NOT NULL
);
CREATE INDEX idx_runs_dataset_time ON runs (dataset_id, started_at DESC);
CREATE INDEX idx_runs_status ON runs (status);

-- The §3.1 dataset-level rollup: one row per run, holding the *scalar* facts
-- only (row/column counts with their ADR-015 method label, declared grain, key
-- candidates, temporal coverage window). The per-column metric bodies are
-- artifact content (M3-T2) and normalised metrics land in M3-T3.
CREATE TABLE dataset_profiles (
    run_id                     TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    schema_version             TEXT NOT NULL,
    row_count                  INTEGER,
    row_count_method           TEXT,                  -- exact|approximate|sampled|not_computed
    row_count_reason           TEXT,
    column_count               INTEGER,
    grain                      TEXT,
    primary_key_candidates_json TEXT,                 -- JSON array or NULL (never fabricated)
    temporal_column            TEXT,
    temporal_min               TEXT,
    temporal_max               TEXT,
    temporal_provenance_json   TEXT,
    source_metadata_json       TEXT
);

-- Artifact references (ADR-008): the big results live as FILES; this is the
-- row that points at them. ``ref_path`` is store-relative, never absolute,
-- so moving the user-data directory keeps the reference resolvable.
CREATE TABLE run_artifacts (
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                        -- profile_json | html_report | issue_csv | …
    ref_path    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, kind, ref_path)
);

-- Per-task error log (§6.4: a task failure rides here, it never demotes the
-- run) and the run's warning strings.
CREATE TABLE run_errors (
    run_id  TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq     INTEGER NOT NULL,
    code    TEXT NOT NULL,
    task    TEXT,
    message TEXT,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE run_warnings (
    run_id  TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq     INTEGER NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

-- Column inventory of a run: identity + the physical/logical/semantic split
-- (MVP acceptance requires it visible in the UI). Metric *values* are M3-T3;
-- histograms/categories stay in the artifact document.
CREATE TABLE run_columns (
    run_id          TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    ordinal         INTEGER NOT NULL,
    column_name     TEXT NOT NULL,
    physical_type   TEXT NOT NULL,
    logical_type    TEXT NOT NULL,
    semantic_type   TEXT NOT NULL,
    unit_json       TEXT,                             -- §3.2 unit assertion or NULL
    PRIMARY KEY (run_id, ordinal)
);
CREATE INDEX idx_run_columns_name ON run_columns (run_id, column_name);

-- Quality checks (§4.1), one row per rule per run. FAIL ≠ ERROR is preserved
-- by the status column + the nullable error code/message pair.
CREATE TABLE quality_checks (
    run_id                 TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    rule_id                TEXT NOT NULL,
    schema_version         TEXT NOT NULL,
    rule_version           TEXT NOT NULL,
    dimension              TEXT NOT NULL,
    status                 TEXT NOT NULL,
    severity               TEXT NOT NULL,
    column_name            TEXT,                      -- NULL = dataset-scoped rule
    observed_value_json    TEXT,                      -- JSON scalar, or NULL when absent
    observed_method        TEXT,                      -- ADR-015
    observed_method_params_json TEXT,                 -- ADR-015: sampled keeps its seed here
    observed_reason        TEXT,                      -- not_computed reason
    observed_denominator   INTEGER,
    threshold_operator     TEXT,
    threshold_value_json   TEXT,
    threshold_upper_json   TEXT,
    failing_record_count   INTEGER,
    error_code             TEXT,
    error_message          TEXT,
    executed_at            TEXT NOT NULL,
    evidence_denominator   INTEGER,
    evidence_baseline_ref  TEXT,
    present_keys_json      TEXT NOT NULL,             -- §4.1 document key set
    evidence_present_keys_json TEXT,                  -- §4.1 evidence block key set
    PRIMARY KEY (run_id, rule_id)
);
CREATE INDEX idx_checks_status ON quality_checks (run_id, status);
CREATE INDEX idx_checks_dimension ON quality_checks (run_id, dimension);

CREATE TABLE check_evidence_refs (
    run_id  TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    ref     TEXT NOT NULL,
    PRIMARY KEY (run_id, rule_id, seq),
    FOREIGN KEY (run_id, rule_id) REFERENCES quality_checks(run_id, rule_id) ON DELETE CASCADE
);

-- Findings metadata (§4.2). ``fingerprint`` (ADR-016) is indexed, NOT unique:
-- the same problem recurring across runs is exactly what baseline/drift keys
-- on, so one fingerprint legitimately owns many rows.
CREATE TABLE findings (
    run_id             TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    finding_id         TEXT NOT NULL,
    schema_version     TEXT NOT NULL,
    fingerprint        TEXT NOT NULL,                 -- sha256:<64 hex>
    title              TEXT NOT NULL,
    summary            TEXT NOT NULL,
    category           TEXT NOT NULL,
    severity           TEXT NOT NULL,
    confidence         TEXT NOT NULL,
    scope_column       TEXT,
    affected_records   INTEGER,
    scope_period_json  TEXT,
    interpretation     TEXT,                          -- nullable in §4.2
    workflow_status    TEXT,                          -- MVP: NULL (Final App §4.5)
    workflow_json      TEXT,                          -- §4.5 object; text "null" = explicit workflow=null
    present_keys_json  TEXT NOT NULL,                 -- §4.2 document key set
    scope_present_keys_json TEXT,                     -- §4.2 scope block key set
    PRIMARY KEY (run_id, finding_id)
);
CREATE INDEX idx_findings_fingerprint ON findings (fingerprint, run_id);
CREATE INDEX idx_findings_run_severity ON findings (run_id, severity);

CREATE TABLE finding_evidence_refs (
    run_id      TEXT NOT NULL,
    finding_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    ref         TEXT NOT NULL,
    PRIMARY KEY (run_id, finding_id, seq),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id) ON DELETE CASCADE
);

-- Facts / interpretation / hypotheses stay structurally separate (CONTRACTS §7):
-- three tables, no merged prose, and a fact always carries its provenance.
CREATE TABLE finding_facts (
    run_id      TEXT NOT NULL,
    finding_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    metric_ref  TEXT NOT NULL,
    value_json  TEXT,
    method      TEXT NOT NULL,
    method_params_json TEXT,
    reason      TEXT,
    present_keys_json TEXT NOT NULL,                  -- §4.2 fact key set
    PRIMARY KEY (run_id, finding_id, seq),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id) ON DELETE CASCADE
);

CREATE TABLE finding_hypotheses (
    run_id          TEXT NOT NULL,
    finding_id      TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    text            TEXT NOT NULL,
    status          TEXT NOT NULL,                    -- MVP: unverified
    suggested_probe TEXT,
    present_keys_json TEXT NOT NULL,                  -- §4.2 hypothesis key set
    PRIMARY KEY (run_id, finding_id, seq),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id) ON DELETE CASCADE
);

CREATE TABLE finding_related_refs (
    run_id            TEXT NOT NULL,
    finding_id        TEXT NOT NULL,
    seq               INTEGER NOT NULL,
    related_finding_id TEXT NOT NULL,
    PRIMARY KEY (run_id, finding_id, seq),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id) ON DELETE CASCADE
);

CREATE TABLE finding_recommendation_refs (
    run_id              TEXT NOT NULL,
    finding_id          TEXT NOT NULL,
    seq                 INTEGER NOT NULL,
    recommendation_id   TEXT NOT NULL,
    PRIMARY KEY (run_id, finding_id, seq),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id) ON DELETE CASCADE
);

-- Recommendations (§4.3). ``reason_type`` is kept so a consumer can tell an
-- evidence-cited action from one requiring business context, and evidence refs
-- are rows so "which recommendation cited this metric?" is a join.
CREATE TABLE recommendations (
    run_id              TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    recommendation_id   TEXT NOT NULL,
    schema_version      TEXT NOT NULL,
    finding_id          TEXT NOT NULL,
    action              TEXT NOT NULL,
    rationale           TEXT NOT NULL,
    reason_type         TEXT NOT NULL,
    target_improvement  TEXT,
    status              TEXT NOT NULL,
    present_keys_json   TEXT NOT NULL,                -- §4.3 document key set
    PRIMARY KEY (run_id, recommendation_id)
);

CREATE TABLE recommendation_evidence_refs (
    run_id             TEXT NOT NULL,
    recommendation_id  TEXT NOT NULL,
    seq                INTEGER NOT NULL,
    ref                TEXT NOT NULL,
    PRIMARY KEY (run_id, recommendation_id, seq),
    FOREIGN KEY (run_id, recommendation_id)
        REFERENCES recommendations(run_id, recommendation_id) ON DELETE CASCADE
);

CREATE TABLE recommendation_do_not (
    run_id             TEXT NOT NULL,
    recommendation_id  TEXT NOT NULL,
    seq                INTEGER NOT NULL,
    text               TEXT NOT NULL,
    PRIMARY KEY (run_id, recommendation_id, seq),
    FOREIGN KEY (run_id, recommendation_id)
        REFERENCES recommendations(run_id, recommendation_id) ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# v2 — M3-T3 normalised (run_id, column_name, metric_name) value rows
# ---------------------------------------------------------------------------
#
# ADR-008 keeps the *full* ColumnProfile document (histograms, category lists,
# every metric body) in the artifact FILE store; v1 deliberately stored only
# identity/type in ``run_columns`` and the §3.1 scalars in ``dataset_profiles``.
# That leaves run-history *diffing* (M6) with no cheap way to read one numeric
# cell: comparing `deal_value`'s p95 across two runs would otherwise mean
# parsing and walking the whole ``profile.json`` artifact every time.
#
# This table is the missing index, not a second source of truth: it promotes
# each CONTRACTS §3.2 ``MetricValue`` leaf (value + provenance pair) to one
# addressed row keyed ``(run_id, column_name, metric_name)``, carrying the
# ADR-015 ``method`` + ``method_params`` (+ seed lives inside method_params) so
# a diff reads a single cell with a ``SELECT``. The artifact stays authoritative
# for anything not promoted here (histogram bins, category arrays, the
# physical/logical/semantic type split — all still in v1/§3.1/artifact). It is
# purely additive: v1 is never edited (an existing user file carries its stamp).
#
# ``metric_name`` is the §3.2 path of the metric within its column
# (``missingness.missing_rate``, ``uniqueness.distinct_count``, ``stats.p95``,
# ``distribution.skewness`` …) — the same dotted identity the Finding ``metric_ref``
# already uses, so a diff cell and an evidence citation describe one thing.
#
# Absence vs null is kept exactly as the §1 convention demands (mirrors the
# ``quality_checks`` observed-value columns): ``value_json`` NULL means the
# ``value`` key was absent (the ``not_computed`` case), the text ``"null"`` means
# an explicit null; ``reason`` carries why a value was not computed.
_V2_SQL = """
CREATE TABLE column_metric_values (
    run_id               TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    column_name          TEXT NOT NULL,
    metric_name          TEXT NOT NULL,                -- §3.2 path within the column
    value_json           TEXT,                         -- JSON scalar; NULL = key absent; "null" = explicit null
    method               TEXT NOT NULL,                -- ADR-015: exact|approximate|sampled|not_computed
    method_params_json   TEXT,                         -- ADR-015: sampled keeps its seed here
    reason               TEXT,                         -- not_computed reason
    denominator          INTEGER,                      -- §1 optional MetricValue denominator
    PRIMARY KEY (run_id, column_name, metric_name)
);

-- Diff reads one metric across the two runs being compared, then across the
-- column inventory of a run; these two access paths are the M6 screen.
CREATE INDEX idx_metric_values_run_metric ON column_metric_values (run_id, metric_name);
CREATE INDEX idx_metric_values_column_metric ON column_metric_values (column_name, metric_name);
"""

#: The released migration chain. **Append-only** — never edit an entry that
#: has shipped: existing user files carry its stamp.
MIGRATIONS: Tuple[Migration, ...] = (
    Migration(version=1, name="m3t1_initial_metadata", sql=_V1_SQL),
    Migration(version=2, name="m3t3_column_metric_values", sql=_V2_SQL),
)

#: Highest DDL version this build knows how to produce.
TARGET_VERSION = max(m.version for m in MIGRATIONS)


def ensure_migration_table(conn: sqlite3.Connection) -> None:
    """Create the bookkeeping table if absent (idempotent; runs before any
    version read so a brand-new file reports ``0`` rather than failing)."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
            version        INTEGER PRIMARY KEY,
            name           TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            applied_at     TEXT NOT NULL
        )
        """
    )


def current_version(conn: sqlite3.Connection) -> int:
    """Highest applied DDL version, or ``0`` for an untracked/empty database."""
    ensure_migration_table(conn)
    row = conn.execute(
        f"SELECT MAX(version) FROM {MIGRATION_TABLE}"
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def applied_migrations(conn: sqlite3.Connection) -> List[dict]:
    """Full migration history, oldest first (audit/support view)."""
    ensure_migration_table(conn)
    columns = ("version", "name", "schema_version", "applied_at")
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM {MIGRATION_TABLE} ORDER BY version"
    ).fetchall()
    return [dict(zip(columns, row)) for row in rows]


def _split_statements(sql: str) -> List[str]:
    """DDL script → executable statements.

    Comments are stripped **per line, including trailing ones** (a ``--`` note
    in these scripts contains ``;`` — splitting first would cut a statement in
    half), then the remainder is split on ``;``. The scripts are our own and
    hold no ``--`` or ``;`` inside string literals, so a line-oriented pass is
    exact here and keeps every statement individually executable —
    ``executescript`` would implicitly commit, which breaks the atomic
    migration transaction this module promises.
    """
    cleaned = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]


def backup_database(
    conn: sqlite3.Connection,
    *,
    before_version: int,
    backups_root: "Path | str",
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Copy the live database into ``backups_root`` before it is altered
    (SECURITY §6.5 "keep a pre-migration backup of SQLite").

    Uses the SQLite online-backup API so the copy is consistent even while
    other connections read (WAL). Returns ``None`` for an in-memory database,
    which has no file to preserve."""
    source = conn.execute("PRAGMA database_list").fetchone()
    # row = (index, name, file); the main db may be "" for :memory:
    db_file = (source[2] if source and len(source) > 2 else None) or ""
    if not db_file:
        return None
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    target = Path(backups_root).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    dest = target / f"metadata.pre-migration-v{before_version}-{stamp}.sqlite3"
    dest_conn = sqlite3.connect(dest)
    try:
        with dest_conn:
            conn.backup(dest_conn)
    finally:
        dest_conn.close()
    return dest


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    migrations: Sequence[Migration] = MIGRATIONS,
    backups_root: "Path | str | None" = None,
    contract_schema_version: str = CONTRACT_SCHEMA_VERSION,
) -> MigrationReport:
    """Bring ``conn``'s schema up to the newest version in ``migrations``.

    Forward-only. An *existing* version above the target means the file was
    written by a newer engine: refuse (SECURITY §6.5 documents
    restore-from-backup as the downgrade path — silently dropping tables a
    newer version added would corrupt a user's history). Each migration runs
    in its own ``BEGIN IMMEDIATE`` transaction, so the stamp and the DDL land
    together and a crash leaves the previous version intact.

    ``backups_root`` — when given and at least one migration is pending, the
    database is backed up first.
    """
    ensure_migration_table(conn)
    before = current_version(conn)
    target = max((m.version for m in migrations), default=0)
    backup_path: Optional[Path] = None
    if before > target:
        raise MigrationError(
            f"database schema version {before} is newer than this engine supports "
            f"({target}); restore a pre-migration backup instead of downgrading"
        )
    if before == 0 and target > 0:
        # A file that already owns tables but carries no stamp is not a
        # database this mechanism may alter — refuse rather than guess.
        known = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != ?",
            (MIGRATION_TABLE,),
        ).fetchone()[0]
        if known:
            raise MigrationError(
                "database contains unversioned tables and no migration history; "
                "refusing to migrate an unknown schema"
            )

    pending = sorted((m for m in migrations if m.version > before), key=lambda m: m.version)
    if not pending:
        return MigrationReport(before=before, after=before)

    seen = {m.version for m in pending}
    if len(seen) != len(pending):
        raise MigrationError("duplicate migration versions in the chain")
    expected = before + 1
    for m in pending:
        if m.version != expected:
            raise MigrationError(
                f"migration chain has a gap: expected version {expected}, found {m.version}"
            )
        expected += 1

    if backups_root is not None and before > 0:
        # Nothing to preserve on a brand-new file (SECURITY §6.5 asks for a
        # backup before a *migration*, not before a first create).
        backup_path = backup_database(conn, before_version=before, backups_root=backups_root)

    # Manual transaction control: the DDL, the stamp and the commit must be one
    # unit, which requires autocommit mode (the default legacy isolation would
    # implicitly commit around DDL and around ``BEGIN``).
    previous_isolation = conn.isolation_level
    conn.isolation_level = None
    applied: List[Tuple[int, str]] = []
    try:
        for m in pending:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _split_statements(m.sql):
                    conn.execute(statement)
                conn.execute(
                    f"INSERT INTO {MIGRATION_TABLE} "
                    "(version, name, schema_version, applied_at) VALUES (?, ?, ?, ?)",
                    (
                        m.version,
                        m.name,
                        contract_schema_version,
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ),
                )
            except sqlite3.Error as exc:
                conn.execute("ROLLBACK")
                # The host and the worker both open the store, so a cold start
                # can have two processes race this very migration. If the stamp
                # is there now, the peer's transaction landed the DDL — that is
                # the outcome we wanted, not a failure to crash the app with.
                if current_version(conn) >= m.version:
                    applied.append((m.version, m.name))
                    continue
                raise MigrationError(
                    f"migration {m.version} ({m.name}) failed: {exc}"
                ) from exc
            conn.execute("COMMIT")
            applied.append((m.version, m.name))
    finally:
        conn.isolation_level = previous_isolation

    after = current_version(conn)
    conn.execute(f"PRAGMA user_version = {int(after)}")
    return MigrationReport(
        before=before, after=after, applied=tuple(applied), backup_path=backup_path
    )


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "MIGRATION_TABLE",
    "MIGRATIONS",
    "TARGET_VERSION",
    "Migration",
    "MigrationError",
    "MigrationReport",
    "ensure_migration_table",
    "current_version",
    "applied_migrations",
    "backup_database",
    "apply_migrations",
]
