"""Persistence layer (ARCHITECTURE §3 ``storage/``).

M3-T1 lands the **metadata store**: the SQLite schema (dataset registry, config
versions, jobs, runs, checks/findings metadata, artifact references) plus its
forward-only migration mechanism, and the directory-resolution rules that keep
user data outside the application bundle.

* :mod:`~data_profiling_engine.storage.paths` — OS-standard user-data / cache /
  temp directories (SECURITY §2) + the app-bundle separation guard.
* :mod:`~data_profiling_engine.storage.migrations` — versioned DDL chain,
  pre-migration backup, downgrade refusal (SECURITY §6.5, ADR-008).
* :mod:`~data_profiling_engine.storage.metadata_store` — typed writer/reader
  for one profiling run and everything it produced (ADR-008); also promotes
  normalised `(run_id, column, metric)` value rows so a run-history diff reads
  one numeric cell without parsing the artifact (M3-T3).
* :mod:`~data_profiling_engine.storage.artifact_store` — the file side of
  ADR-008: the per-run artifact layout, its lifecycle/retention policy, and the
  cleanup entry points (SECURITY §2/§4/§6.5, M3-T2).

Not here yet, by milestone order: none — M3 (persistence) is complete.
"""
from data_profiling_engine.storage.artifact_store import (
    DEFAULT_FILENAMES,
    KIND_RETENTION,
    STORE_PREFIX,
    ArtifactStore,
    ArtifactStoreError,
    RetentionPolicy,
    retention_for,
)
from data_profiling_engine.storage.metadata_store import (
    DEFAULT_ARTIFACT_KIND,
    DEFAULT_BUSY_TIMEOUT_MS,
    OPEN_JOB_STATES,
    TERMINAL_JOB_STATES,
    MetadataIntegrityError,
    MetadataStore,
    MetadataStoreError,
    config_document_hash,
)
from data_profiling_engine.storage.migrations import (
    CONTRACT_SCHEMA_VERSION,
    MIGRATION_TABLE,
    MIGRATIONS,
    TARGET_VERSION,
    Migration,
    MigrationError,
    MigrationReport,
    applied_migrations,
    apply_migrations,
    backup_database,
    current_version,
)
from data_profiling_engine.storage.paths import (
    ARTIFACT_SUBDIR,
    BACKUP_SUBDIR,
    DEFAULT_APP_NAME,
    METADATA_DB_NAME,
    DirectoryPolicyError,
    app_bundle_dir,
    artifacts_dir,
    backups_dir,
    default_cache_root,
    default_data_root,
    default_temp_root,
    ensure_outside_app_bundle,
    ensure_private_dir,
    metadata_db_path,
    resolve_data_root,
)

__all__ = [
    # migrations
    "CONTRACT_SCHEMA_VERSION",
    "MIGRATION_TABLE",
    "MIGRATIONS",
    "TARGET_VERSION",
    "Migration",
    "MigrationError",
    "MigrationReport",
    "applied_migrations",
    "apply_migrations",
    "backup_database",
    "current_version",
    # paths
    "ARTIFACT_SUBDIR",
    "BACKUP_SUBDIR",
    "DEFAULT_APP_NAME",
    "METADATA_DB_NAME",
    "DirectoryPolicyError",
    "app_bundle_dir",
    "artifacts_dir",
    "backups_dir",
    "default_cache_root",
    "default_data_root",
    "default_temp_root",
    "ensure_outside_app_bundle",
    "ensure_private_dir",
    "metadata_db_path",
    "resolve_data_root",
    # metadata store
    "DEFAULT_ARTIFACT_KIND",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "OPEN_JOB_STATES",
    "TERMINAL_JOB_STATES",
    "MetadataIntegrityError",
    "MetadataStore",
    "MetadataStoreError",
    "config_document_hash",
    # artifact file store (M3-T2)
    "STORE_PREFIX",
    "DEFAULT_FILENAMES",
    "KIND_RETENTION",
    "RetentionPolicy",
    "ArtifactStore",
    "ArtifactStoreError",
    "retention_for",
]
