"""M3-T1 tests — SQLite metadata schema + migrations (ADR-008; SECURITY §2, §6.5).

Three layers of guarantee, in the order the specs ask for them
(TESTING §1 "SQLite persistence, artifact lifecycle" integration row, §3.5
contract validation, §3.3 reproducibility):

1. **Directories** (SECURITY §2) — user data resolves from OS-standard
   locations, is overridable for packaging/tests, and is refused inside the
   application bundle so an upgrade can never destroy it (§6.5).
2. **Migrations** (ADR-008 "migration mechanism required") — forward-only DDL
   chain with an atomic per-migration transaction, a pre-migration backup,
   refusal to downgrade or to touch an unversioned file, and a gapless chain.
3. **The metadata store** — registry / config versions / jobs / runs / checks /
   findings written *decomposed* into rows, artifact results referenced as
   files, and every block read back as a schema-valid contract object identical
   to what went in. The read-back tests are the point: they prove SQLite holds
   the whole §2/§4.1/§4.2/§4.3 record, not a lossy summary, while ADR-015
   provenance labels and the FAIL ≠ ERROR split survive the round trip.

Concurrency (ADR-008 "concurrent read during run") is covered by a writer thread
plus a reader store on the same file, in WAL mode.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional

import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.orchestration.rollup import (
    SCHEMA_VERSION,
    ProfileBundle,
    config_hash,
    profile_dataset,
)
from data_profiling_engine.schemas.models import (
    CheckError,
    CheckEvidence,
    CheckThreshold,
    ColumnProfile,
    DatasetConfig,
    DatasetProfile,
    Distribution,
    Finding,
    FindingFact,
    FindingHypothesis,
    FindingScope,
    JobProgress,
    JobStatus,
    MetricMethod,
    MetricValue,
    Missingness,
    NumericStats,
    ProfilingRun,
    Provenance,
    QualityCheckResult,
    RunConfig,
    RunDataset,
    RunEngine,
    RunErrorEntry,
    RunSampling,
    RunStatus,
    RunTiming,
    Recommendation,
    ReasonType,
    SourceKind,
    SourceSnapshot,
    Uniqueness,
)
from data_profiling_engine.storage import MetadataStore, paths as P
from data_profiling_engine.storage.metadata_store import (
    MetadataIntegrityError,
    MetadataStoreError,
    _iter_column_metrics,
    _validate,
    config_document_hash,
)
from data_profiling_engine.storage.migrations import (
    MIGRATIONS,
    TARGET_VERSION,
    Migration,
    MigrationError,
    apply_migrations,
    backup_database,
    current_version,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _sales_config_doc(mode: str = "standard") -> dict:
    return {
        "config_version": 3,
        "dataset": {
            "name": "sales_deals",
            "grain": "one_row_per_deal",
            "primary_key": ["deal_id"],
        },
        "profiling": {"mode": mode},
        "quality_checks": [
            {"id": "deal_id_unique", "rule": "unique", "column": "deal_id",
             "dimension": "uniqueness", "severity": "critical"},
            {"id": "value_not_null", "rule": "not_null", "column": "deal_value",
             "dimension": "completeness", "severity": "high"},
        ],
    }


def _write_sales_deals(csv: Path, n: int = 60) -> None:
    """Small deterministic ``sales_deals`` fixture (TESTING §2): duplicate ids +
    ~14% missing deal_value, so checks/findings actually exist to persist."""
    stages = ["Prospecting", "Closed Won", "Negotiation"]
    lines = ["deal_id,created_at,stage,deal_value,is_new"]
    for i in range(n):
        did = f"DEAL-{i % (n - 6):04d}" if i >= n - 6 else f"DEAL-{i:04d}"
        value = "" if i % 7 == 0 else str(1000 + i * 7)
        lines.append(
            f"{did},2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d},{stages[i % 3]},{value},1"
        )
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture()
def store(tmp_path: Path) -> MetadataStore:
    st = MetadataStore(tmp_path / "meta" / "metadata.sqlite3")
    try:
        yield st
    finally:
        st.close()


@pytest.fixture()
def sales_bundle(tmp_path: Path):
    """One real engine run, persisted once per test (the M2-T11 spine output).
    The pipeline dominates the cost, so the bundle is built a single time."""
    ws = tmp_path / "ws"
    ws.mkdir()
    csv = ws / "sales_deals.csv"
    _write_sales_deals(csv)
    cfg = DatasetConfig.model_validate(_sales_config_doc())
    bundle = profile_dataset("sales_deals", csv, cfg, "run-m3t1", allowed_roots=[ws])
    return bundle, cfg


# ---------------------------------------------------------------------------
# synthetic contract objects (cheap, exact-shaped fixtures)
# ---------------------------------------------------------------------------


def _prov(method: str = "exact", **kwargs) -> Provenance:
    return Provenance(method=method, **kwargs)


def _mv(value, method: str = "exact", **kwargs) -> MetricValue:
    return MetricValue(value=value, provenance=_prov(method, **kwargs))


def _run_doc(run_id: str = "run-syn", dataset_id: str = "ds_syn", **overrides) -> ProfilingRun:
    base = dict(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        dataset=RunDataset(
            dataset_id=dataset_id,
            source_kind=SourceKind.FILE,
            connector="csv",
            source_ref="/data/sales.csv",
            source_snapshot=SourceSnapshot(
                path="/data/sales.csv",
                size_bytes=1234,
                modified_at="2026-10-01T00:00:00Z",
                content_hash="sha256:" + "a" * 64,
            ),
        ),
        config=RunConfig(
            config_version="3",
            config_hash="sha256:" + "b" * 64,
            profile_mode="standard",
        ),
        engine=RunEngine(engine_version="0.1.0", duckdb_version="1.1.3", python_version="3.12.7"),
        timing=RunTiming(
            started_at="2026-10-24T09:00:00Z",
            finished_at="2026-10-24T09:00:05Z",
            duration_ms=5000,
        ),
        status=RunStatus.SUCCEEDED,
        result_refs={"profile_json": "artifacts/run-syn/profile.json"},
    )
    base.update(overrides)
    return ProfilingRun.model_validate(base)


def _profile_doc(run_id: str = "run-syn", **overrides) -> DatasetProfile:
    base = dict(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        row_count=_mv(200),
        column_count=2,
        grain="one_row_per_deal",
        primary_key_candidates=["deal_id"],
        columns=[
            ColumnProfile(
                schema_version=SCHEMA_VERSION, column="deal_id", ordinal=0,
                physical_type="VARCHAR", logical_type="identifier",
                semantic_type="identifier",
                uniqueness=Uniqueness(distinct_count=_mv(188), is_unique_candidate=False),
            ),
            ColumnProfile(
                schema_version=SCHEMA_VERSION, column="deal_value", ordinal=1,
                physical_type="VARCHAR", logical_type="numeric_currency",
                semantic_type="measure",
            ),
        ],
    )
    base.update(overrides)
    return DatasetProfile.model_validate(base)


def _check_pass(rule_id: str = "deal_id_unique", run_id: str = "run-syn", **overrides) -> QualityCheckResult:
    base = dict(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        rule_id=rule_id,
        rule_version="1",
        dimension="uniqueness",
        status="PASS",
        severity="critical",
        column="deal_id",
        observed=_mv(0.0),
        threshold=CheckThreshold(operator="==", value=0),
        failing_record_count=0,
        evidence=CheckEvidence(denominator=200, sample_row_refs=["row:1"]),
        executed_at="2026-10-24T09:00:04Z",
    )
    base.update(overrides)
    return QualityCheckResult.model_validate(base)


def _check_error(rule_id: str = "ghost_rule", run_id: str = "run-syn") -> QualityCheckResult:
    return QualityCheckResult.model_validate(dict(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        rule_id=rule_id,
        rule_version="1",
        dimension="completeness",
        status="ERROR",
        severity="high",
        column="does_not_exist",
        executed_at="2026-10-24T09:00:04Z",
        error=CheckError(code="RULE_EXEC_FAILED", message="column not found in source"),
    ))


def _finding(
    finding_id: str = "F-001",
    run_id: str = "run-syn",
    fingerprint: Optional[str] = None,
    **overrides,
) -> Finding:
    base = dict(
        schema_version=SCHEMA_VERSION,
        finding_id=finding_id,
        fingerprint=fingerprint or "sha256:" + "c" * 64,
        run_id=run_id,
        title="Deal value incomplete",
        summary="15% of deal_value is empty, above the configured threshold.",
        category="completeness",
        severity="high",
        confidence="high",
        scope=FindingScope(column="deal_value", affected_records=30, period=None),
        facts=[FindingFact(metric_ref="missingness.missing_rate:deal_value", value=0.15,
                           provenance=_prov("exact"))],
        interpretation=None,
        hypotheses=[FindingHypothesis(text="Sync may drop the amount field",
                                      status="unverified", suggested_probe="compare CRM export")],
        evidence_refs=["rule:value_not_null", "missingness.missing_rate:deal_value"],
        recommendation_ids=["R-001"],
    )
    base.update(overrides)
    return Finding.model_validate(base)


def _recommendation(
    recommendation_id: str = "R-001", finding_id: str = "F-001", **overrides
) -> Recommendation:
    base = dict(
        schema_version=SCHEMA_VERSION,
        recommendation_id=recommendation_id,
        finding_id=finding_id,
        action="Investigate deal_value completeness in the CRM export.",
        rationale="Missing rate exceeds the configured threshold (rule:value_not_null).",
        reason_type=ReasonType.EVIDENCE,
        target_improvement="Bring unexplained missingness below threshold.",
        do_not=["Do not backfill with zero"],
        evidence_refs=["rule:value_not_null"],
        status="proposed",
    )
    base.update(overrides)
    return Recommendation.model_validate(base)


def _bundle(**overrides) -> ProfileBundle:
    run = overrides.pop("run", None) or _run_doc()
    profile = overrides.pop("profile", None) or _profile_doc(run_id=run.run_id)
    checks = list(overrides.pop("checks", None) or [])
    findings = list(overrides.pop("findings", None) or [])
    recommendations = list(overrides.pop("recommendations", None) or [])
    errors = list(overrides.pop("errors", None) or [])
    assert not overrides, f"unexpected bundle overrides {sorted(overrides)}"
    # CONTRACTS §1 scopes every child object to its run, so a test that renames
    # the run must not leave blocks citing the old id (the FK would, and does,
    # reject that — binding here keeps the fixtures coherent)
    profile = profile.model_copy(update={"run_id": run.run_id})
    checks = [c.model_copy(update={"run_id": run.run_id}) for c in checks]
    findings = [f.model_copy(update={"run_id": run.run_id}) for f in findings]
    return ProfileBundle(
        run=run, profile=profile,
        checks=tuple(checks), findings=tuple(findings),
        recommendations=tuple(recommendations), errors=tuple(errors),
    )


# ===========================================================================
# 1. Directory separation (SECURITY §2)
# ===========================================================================


class TestDirectoryPolicy:
    def test_data_root_uses_os_standard_location(self, monkeypatch):
        monkeypatch.delenv(P.DATA_DIR_ENV, raising=False)
        root = P.default_data_root(app_name="UnitApp")
        assert root.name == "UnitApp"
        assert str(Path.home()) in str(root)
        import platform
        if platform.system() == "Darwin":
            assert root == Path.home() / "Library" / "Application Support" / "UnitApp"

    def test_env_override_wins_and_is_separate_from_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv(P.DATA_DIR_ENV, str(tmp_path / "user-data"))
        monkeypatch.setenv(P.CACHE_DIR_ENV, str(tmp_path / "user-cache"))
        assert P.default_data_root() == (tmp_path / "user-data").resolve()
        assert P.default_cache_root() == (tmp_path / "user-cache").resolve()
        assert P.metadata_db_path().parent == (tmp_path / "user-data").resolve()

    def test_metadata_db_path_under_data_root(self, tmp_path):
        assert P.metadata_db_path(tmp_path) == tmp_path / P.METADATA_DB_NAME
        assert P.backups_dir(tmp_path) == tmp_path / P.BACKUP_SUBDIR
        assert P.artifacts_dir(tmp_path) == tmp_path / P.ARTIFACT_SUBDIR

    def test_data_root_inside_app_bundle_is_refused(self, tmp_path, monkeypatch):
        bundle = tmp_path / "App.app" / "Contents" / "Resources"
        bundle.mkdir(parents=True)
        monkeypatch.setattr(P, "app_bundle_dir", lambda: bundle)
        with pytest.raises(P.DirectoryPolicyError):
            P.ensure_outside_app_bundle(bundle / "user-data", label="user data")
        # a sibling directory is fine — the guard is containment, not location
        assert P.ensure_outside_app_bundle(tmp_path / "user-data", label="user data")

    def test_store_creates_private_directory_outside_bundle(self, tmp_path):
        import os
        import platform

        target = tmp_path / "meta" / "metadata.sqlite3"
        with MetadataStore(target) as st:
            assert st.db_path == target
            assert target.exists()
        if platform.system() != "Windows":  # POSIX-only permission check
            assert os.stat(target.parent).st_mode & 0o077 == 0

    def test_store_refuses_database_inside_app_bundle(self, tmp_path, monkeypatch):
        bundle = tmp_path / "App.app"
        (bundle / "data").mkdir(parents=True)
        monkeypatch.setattr(P, "app_bundle_dir", lambda: bundle)
        with pytest.raises(P.DirectoryPolicyError):
            MetadataStore(bundle / "data" / "metadata.sqlite3")


# ===========================================================================
# 2. Migration mechanism (ADR-008, SECURITY §6.5)
# ===========================================================================


class TestMigrations:
    def test_fresh_database_reaches_target_version(self, store):
        assert store.schema_version() == TARGET_VERSION == 2
        assert store.migration_report.applied == (
            (1, "m3t1_initial_metadata"),
            (2, "m3t3_column_metric_values"),
        )

    def test_all_metadata_tables_exist(self, store):
        names = {
            r["name"]
            for r in store._query("SELECT name FROM sqlite_master WHERE type='table'")
        }
        expected = {
            "datasets", "dataset_config_versions", "jobs", "runs", "dataset_profiles",
            "run_columns", "column_metric_values", "quality_checks", "check_evidence_refs", "findings",
            "finding_evidence_refs", "finding_facts", "finding_hypotheses",
            "finding_related_refs", "finding_recommendation_refs", "recommendations",
            "recommendation_evidence_refs", "recommendation_do_not", "run_errors",
            "run_warnings", "run_artifacts", "schema_migrations",
        }
        assert expected <= names

    def test_reopen_applies_nothing_and_keeps_data(self, tmp_path):
        db = tmp_path / "metadata.sqlite3"
        with MetadataStore(db) as st:
            st.register_dataset(dataset_id="ds_a", name="A", connector="csv", source_ref="/a.csv")
        with MetadataStore(db) as st2:
            assert st2.migration_report.changed is False
            assert st2.get_dataset("ds_a")["dataset_id"] == "ds_a"

    def test_wal_and_foreign_keys_enabled(self, store):
        assert store.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_upgrade_with_appended_migration(self, tmp_path):
        db = tmp_path / "metadata.sqlite3"
        extra = Migration(3, "m_test_additions", "CREATE TABLE drift_marks (run_id TEXT, note TEXT);")
        with MetadataStore(db, migrations=MIGRATIONS + (extra,)) as st:
            assert st.schema_version() == 3
            assert [m["version"] for m in st.migration_history()] == [1, 2, 3]
        # reopening with the shipped chain only = the downgrade case
        with pytest.raises(MigrationError):
            MetadataStore(db, migrations=MIGRATIONS)

    def test_pre_migration_backup_is_written_and_readable(self, tmp_path):
        db = tmp_path / "meta" / "metadata.sqlite3"
        with MetadataStore(db) as st:
            st.register_dataset(dataset_id="ds_a", name="A", connector="csv", source_ref="/a.csv")
            assert st.migration_report.backup_path is None  # first create = nothing to preserve
        extra = Migration(3, "m_three", "CREATE TABLE t3 (x TEXT);")
        with MetadataStore(db, migrations=MIGRATIONS + (extra,)) as st:
            backup = st.migration_report.backup_path
            assert backup is not None
            assert backup.parent.name == P.BACKUP_SUBDIR
        files = sorted((tmp_path / "meta" / P.BACKUP_SUBDIR).glob("metadata.pre-migration-v2-*.sqlite3"))
        assert [f.name for f in files] == [backup.name]
        con = sqlite3.connect(backup)
        try:
            assert con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 1
            assert current_version(con) == 2  # the copy is the pre-migration state
        finally:
            con.close()

    def test_backup_helper_skips_in_memory_database(self, tmp_path):
        con = sqlite3.connect(":memory:")
        assert backup_database(con, before_version=0, backups_root=tmp_path) is None

    def test_migration_is_atomic_on_failure(self, tmp_path):
        db = tmp_path / "metadata.sqlite3"
        bad = Migration(3, "m_bad", "CREATE TABLE good_table(x); CREATE TABLE good_table(y);")
        con = sqlite3.connect(db)
        try:
            apply_migrations(con, migrations=MIGRATIONS)
            with pytest.raises(MigrationError):
                apply_migrations(con, migrations=MIGRATIONS + (bad,))
            assert current_version(con) == 2
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "good_table" not in names  # rolled back with the failed statement
        finally:
            con.close()

    def test_two_openers_migrating_the_same_file_both_succeed(self, tmp_path):
        """Cold start: the host and the worker each open the store, so the loser
        of the migration race must accept the peer's stamp, not crash (§6.5)."""
        db = tmp_path / "metadata.sqlite3"
        results: List[int] = []
        errors: List[BaseException] = []
        barrier = threading.Barrier(2)

        def open_store() -> None:
            try:
                barrier.wait(timeout=30)
                with MetadataStore(db) as st:
                    results.append(st.schema_version())
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=open_store) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
            assert not t.is_alive()
        assert errors == []
        assert results == [TARGET_VERSION, TARGET_VERSION]
        con = sqlite3.connect(db)
        try:
            stamps = con.execute("SELECT version, name FROM schema_migrations").fetchall()
        finally:
            con.close()
        assert stamps == [
            (1, "m3t1_initial_metadata"), (2, "m3t3_column_metric_values")
        ]  # each stamped exactly once

    def test_gap_in_chain_is_refused(self, tmp_path):
        con = sqlite3.connect(tmp_path / "gap.sqlite3")
        try:
            with pytest.raises(MigrationError, match="gap"):
                apply_migrations(con, migrations=(Migration(7, "far", "CREATE TABLE z(x);"),))
        finally:
            con.close()

    def test_unversioned_database_is_not_touched(self, tmp_path):
        db = tmp_path / "legacy.sqlite3"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE runs (run_id TEXT)")
        con.commit()
        try:
            with pytest.raises(MigrationError, match="unversioned"):
                apply_migrations(con)
        finally:
            con.close()

    def test_no_backup_when_nothing_pending(self, tmp_path):
        db = tmp_path / "metadata.sqlite3"
        with MetadataStore(db) as st:
            # brand-new file: §6.5 asks for a backup before a *migration*,
            # not before a first create
            assert st.migration_report.backup_path is None
            assert st.migration_report.before == 0 and st.migration_report.changed is True
        with MetadataStore(db) as st2:
            assert st2.migration_report.backup_path is None
            assert st2.migration_report.changed is False


# ===========================================================================
# 3. Registry, config versions, jobs
# ===========================================================================


class TestRegistryAndJobs:
    def test_dataset_upsert_preserves_first_seen(self, store):
        store.register_dataset(
            dataset_id="ds_a", name="A", connector="csv", source_ref="/a.csv",
            content_hash="sha256:" + "d" * 64, size_bytes=10, modified_at="2026-01-01T00:00:00Z",
            grain="one_row_per_deal", seen_at="2026-10-01T00:00:00Z",
        )
        first = store.get_dataset("ds_a")
        store.register_dataset(
            dataset_id="ds_a", name="A", connector="csv", source_ref="/a.csv",
            content_hash="sha256:" + "e" * 64, size_bytes=20, modified_at="2026-10-10T00:00:00Z",
            seen_at="2026-10-10T00:00:00Z",
        )
        after = store.get_dataset("ds_a")
        assert after["first_seen_at"] == first["first_seen_at"]
        assert after["last_seen_at"] == "2026-10-10T00:00:00Z"
        assert after["content_hash"] == "sha256:" + "e" * 64
        assert after["grain"] == "one_row_per_deal"  # COALESCE keeps the earlier fact

    def test_soft_hide_and_list(self, store):
        store.register_dataset(dataset_id="ds_a", name="A", connector="csv", source_ref="/a.csv")
        store.register_dataset(dataset_id="ds_b", name="B", connector="parquet", source_ref="/b.pq")
        store.set_dataset_active("ds_a", False)
        assert [d["dataset_id"] for d in store.list_datasets()] == ["ds_b"]
        assert len(store.list_datasets(include_inactive=True)) == 2
        with pytest.raises(MetadataStoreError):
            store.set_dataset_active("nope", False)

    def test_registering_dataset_for_unknown_write_is_rejected(self, store):
        with pytest.raises(MetadataStoreError):
            store.create_job("job-1", "ds_missing")

    def test_config_version_round_trip_and_idempotency(self, store):
        store.register_dataset(dataset_id="ds_a", name="A", connector="csv", source_ref="/a.csv")
        cfg = DatasetConfig.model_validate(_sales_config_doc())
        version, digest = store.save_config_version("ds_a", cfg)
        assert (version, digest) == ("3", config_hash(cfg))
        assert store.load_config_version("ds_a", "3").model_dump(mode="json") == cfg.model_dump(mode="json")
        store.save_config_version("ds_a", cfg)
        rows = store._query(
            "SELECT * FROM dataset_config_versions WHERE dataset_id = 'ds_a'"
        )
        assert len(rows) == 1  # identical version+hash is a no-op
        doc = json.loads(rows[0]["document_json"])
        assert not S.validate("dataset_config.schema.json", doc)

    def test_standalone_digest_matches_rollup_config_hash(self, store):
        """§2 pins one digest per logical config: the store's standalone helper
        and the engine's rollup helper must agree or a registry row and its run
        row would cite different hashes for the same config."""
        store.register_dataset(dataset_id="ds_a", name="A", connector="csv", source_ref="/a.csv")
        cfg = DatasetConfig.model_validate(_sales_config_doc())
        version, digest = store.save_config_version("ds_a", cfg)
        assert digest == config_hash(cfg) == config_document_hash(cfg)

    def test_config_version_requires_registered_dataset(self, store):
        cfg = DatasetConfig.model_validate(_sales_config_doc())
        with pytest.raises(MetadataStoreError, match="not registered"):
            store.save_config_version("ds_ghost", cfg)

    def test_job_status_round_trip(self, store):
        store.register_dataset(dataset_id="ds_a", name="A", connector="csv", source_ref="/a.csv")
        store.create_job("job-1", "ds_a")
        status = JobStatus(
            job_id="job-1", status="RUNNING",
            progress=JobProgress(completed_steps=3, total_steps=9, current_step="profiling:stats"),
            result={"run_id": "run-1"},
        )
        store.save_job_status(status)
        loaded = store.load_job_status("job-1")
        assert loaded.model_dump(mode="json") == status.model_dump(mode="json")
        assert not S.validate("job_status.schema.json", loaded.model_dump(mode="json"))

    def test_terminal_job_status_persists_and_leaves_open_set(self, store):
        store.register_dataset(dataset_id="ds_a", name="A", connector="csv", source_ref="/a.csv")
        for job_id, state in [("j-q", "QUEUED"), ("j-r", "RUNNING"), ("j-c", "CANCEL_REQUESTED"),
                              ("j-s", "SUCCEEDED")]:
            store.create_job(job_id, "ds_a")
        for job_id, state in [("j-r", "RUNNING"), ("j-c", "CANCEL_REQUESTED"), ("j-s", "SUCCEEDED")]:
            store.save_job_status(
                JobStatus(job_id=job_id, status=state, progress=JobProgress())
            )
        assert {j["job_id"] for j in store.unfinished_jobs()} == {"j-q", "j-r", "j-c"}

    def test_unknown_job_status_update_is_job_not_found(self, store):
        with pytest.raises(MetadataStoreError) as exc:
            store.save_job_status(JobStatus(job_id="ghost", status="RUNNING", progress=JobProgress()))
        assert exc.value.code == ErrorCode.JOB_NOT_FOUND

    def test_job_status_rejects_bad_error_code_shape(self, store):
        store.register_dataset(dataset_id="ds_a", name="A", connector="csv", source_ref="/a.csv")
        store.create_job("job-1", "ds_a")
        # model_construct bypasses the pydantic field validator so the write
        # boundary itself (the JSON-Schema gate) has to be what refuses
        bad = JobStatus.model_construct(
            job_id="job-1", status="FAILED", progress=JobProgress(),
            error_code="not_a_registry_code",
        )
        with pytest.raises(MetadataIntegrityError):
            store.save_job_status(bad)
        assert store.get_job("job-1")["state"] == "QUEUED"  # row untouched


# ===========================================================================
# 4. Run persistence — synthetic bundles (exact shapes, cheap)
# ===========================================================================


class TestSaveRunSynthetic:
    def test_every_block_lands_and_counts_match(self, store):
        bundle = _bundle(
            run=_run_doc(
                error_log=[
                    RunErrorEntry(code="RULE_EXEC_FAILED", task="ghost_rule", message="boom")
                ]
            ),
            checks=[_check_pass(), _check_error()],
            findings=[_finding()],
            recommendations=[_recommendation()],
        )
        run_id = store.save_run(bundle)
        counts = store.counts(run_id)
        assert counts == {
            "dataset_profiles": 1, "run_columns": 2, "column_metric_values": 1,
            "quality_checks": 2, "findings": 1,
            "recommendations": 1, "run_errors": 1, "run_warnings": 0, "run_artifacts": 1,
        }

    def test_run_round_trips_exactly(self, store):
        bundle = _bundle(
            run=_run_doc(
                sampling=RunSampling(enabled=True, strategy="reservoir", sample_size=5000, seed=20261024),
                warnings=["config default applied for unit status"],
                error_log=[RunErrorEntry(code="METRIC_NOT_COMPUTED", task="stats.skewness:x",
                                         message="skewness needs ≥ 3 valid values")],
            ),
        )
        run_id = store.save_run(bundle)
        loaded = store.load_profiling_run(run_id)
        assert loaded.model_dump(mode="json") == bundle.run.model_dump(mode="json")

    def test_empty_but_present_lists_are_not_reported_as_absent(self, store):
        """§1 keeps three states apart: key absent, key null, key with an empty
        list. The run's own ``warnings: []`` must come back as ``[]``, and a run
        that never carried the key must still not have it."""
        explicit = _run_doc(run_id="run-w1", warnings=[], error_log=[])
        store.save_run(_bundle(run=explicit))
        loaded = store.load_profiling_run("run-w1")
        assert loaded.model_dump(mode="json") == explicit.model_dump(mode="json")
        doc = loaded.model_dump(mode="json")
        assert doc["warnings"] == [] and doc["error_log"] == []

        store.save_run(_bundle(run=_run_doc(run_id="run-w2")))
        bare = store.load_profiling_run("run-w2").model_dump(mode="json")
        assert "warnings" not in bare and "error_log" not in bare

    def test_declared_off_sampling_is_not_reported_as_absent(self, store):
        bundle = _bundle(run=_run_doc(sampling=RunSampling(enabled=False)))
        loaded = store.load_profiling_run(store.save_run(bundle))
        assert loaded.sampling is not None
        assert loaded.sampling.enabled is False

    def test_absent_sampling_stays_absent(self, store):
        bundle = _bundle(run=_run_doc())
        loaded = store.load_profiling_run(store.save_run(bundle))
        assert "sampling" not in loaded.model_dump(mode="json")

    def test_profile_summary_scalars_keep_method_labels(self, store):
        bundle = _bundle(
            profile=_profile_doc(
                row_count=_mv(200, "sampled", method_params={"strategy": "reservoir", "seed": 7}),
                temporal_coverage=None,
            )
        )
        run_id = store.save_run(bundle)
        summary = store.profile_summary(run_id)
        assert summary["row_count"] == 200
        assert summary["row_count_method"] == "sampled"
        assert summary["column_count"] == 2
        assert summary["grain"] == "one_row_per_deal"
        assert summary["primary_key_candidates"] == ["deal_id"]
        assert summary["temporal_coverage"] is None

    def test_not_computed_row_count_has_no_value(self, store):
        row_count = MetricValue(
            provenance=Provenance(method="not_computed", reason="row count unavailable in quick mode")
        )
        bundle = _bundle(profile=_profile_doc(row_count=row_count))
        summary = store.profile_summary(store.save_run(bundle))
        assert summary["row_count"] is None
        assert summary["row_count_method"] == "not_computed"
        assert summary["row_count_reason"] == "row count unavailable in quick mode"

    def test_column_inventory_carries_physical_logical_semantic(self, store):
        run_id = store.save_run(_bundle())
        rows = store.column_inventory(run_id)
        assert [r["ordinal"] for r in rows] == [0, 1]
        assert rows[0]["column_name"] == "deal_id"
        assert rows[0]["physical_type"] == "VARCHAR"
        assert rows[0]["logical_type"] == "identifier"
        assert rows[0]["semantic_type"] == "identifier"

    def test_fail_and_error_are_persisted_apart(self, store):
        run_id = store.save_run(_bundle(checks=[_check_pass(), _check_error()]))
        assert store.check_status_counts(run_id) == {"PASS": 1, "ERROR": 1}
        checks = {c.rule_id: c for c in store.load_check_results(run_id)}
        assert checks["ghost_rule"].status == "ERROR"
        assert checks["ghost_rule"].error.code == "RULE_EXEC_FAILED"
        assert checks["ghost_rule"].observed is None
        assert checks["deal_id_unique"].status == "PASS"
        assert checks["deal_id_unique"].error is None

    def test_check_round_trip_including_threshold_and_evidence(self, store):
        bundle = _bundle(
            checks=[
                _check_pass(
                    observed=_mv(12.0, "exact"),
                    failing_record_count=12,
                    evidence=CheckEvidence(denominator=200, sample_row_refs=["row:2", "row:9"]),
                    status="FAIL",
                )
            ]
        )
        loaded = store.load_check_results(store.save_run(bundle))[0]
        assert loaded.model_dump(mode="json") == bundle.checks[0].model_dump(mode="json")

    def test_missing_rate_check_with_null_threshold_value(self, store):
        check = _check_pass(
            rule_id="value_missing_rate", dimension="completeness", status="WARN",
            observed=_mv(0.14, "exact"),
            threshold=CheckThreshold(operator="<", value=0.05, upper=None),
        )
        loaded = store.load_check_results(store.save_run(_bundle(checks=[check])))[0]
        assert loaded.model_dump(mode="json") == check.model_dump(mode="json")

    def test_finding_round_trip_keeps_structures_separate(self, store):
        finding = _finding(
            scope=FindingScope(column="deal_value", affected_records=30,
                               period={"start": "2024-01-01T00:00:00Z",
                                       "end": "2024-06-30T00:00:00Z"}),
            related_finding_ids=["F-002"],
            interpretation=None,
        )
        loaded = store.load_findings(store.save_run(_bundle(findings=[finding])))[0]
        assert loaded.model_dump(mode="json") == finding.model_dump(mode="json")
        doc = loaded.model_dump(mode="json")
        assert "facts" in doc and "hypotheses" in doc and "interpretation" in doc
        assert doc["scope"]["period"]["start"] == "2024-01-01T00:00:00Z"

    def test_finding_fact_not_computed_carries_reason_not_value(self, store):
        finding = _finding(
            facts=[
                FindingFact(
                    metric_ref="stats.stddev:deal_value",
                    provenance=Provenance(method=MetricMethod.NOT_COMPUTED, reason="no numeric stats"),
                )
            ]
        )
        run_id = store.save_run(_bundle(findings=[finding]))
        row = store._query_one(
            "SELECT method, value_json, reason FROM finding_facts WHERE run_id = ? AND finding_id = ?",
            (run_id, finding.finding_id),
        )
        assert row["value_json"] is None and row["method"] == "not_computed" and row["reason"]
        loaded = store.load_findings(run_id)[0]
        assert loaded.model_dump(mode="json") == finding.model_dump(mode="json")

    def test_recommendation_round_trip(self, store):
        rec = _recommendation(do_not=["Do not backfill with zero", "Do not delete rows"])
        loaded = store.load_recommendations(store.save_run(_bundle(recommendations=[rec])))[0]
        assert loaded.model_dump(mode="json") == rec.model_dump(mode="json")

    def test_artifact_refs_merge_and_override(self, store):
        bundle = _bundle()
        run_id = store.save_run(
            bundle,
            artifact_refs={"html_report": "artifacts/run-syn/report.html",
                           "profile_json": "artifacts/override/profile.json"},
        )
        refs = {a["kind"]: a["ref_path"] for a in store.list_artifacts(run_id)}
        assert refs == {
            "profile_json": "artifacts/override/profile.json",
            "html_report": "artifacts/run-syn/report.html",
        }
        assert store.artifact_ref(run_id, "html_report") == "artifacts/run-syn/report.html"
        # the reference rows are the only artifact data stored — no blob columns
        assert store.load_profiling_run(run_id).result_refs == refs

    def test_run_with_unknown_job_id_is_rejected_atomically(self, store):
        bundle = _bundle(run=_run_doc(run_id="run-dangling", job_id="job-ghost"))
        with pytest.raises(MetadataStoreError) as exc:
            store.save_run(bundle)
        assert exc.value.code == ErrorCode.JOB_NOT_FOUND
        assert store.get_run("run-dangling") is None
        assert store.get_dataset("ds_syn") is None  # registry upsert rolled back too

    def test_save_run_registers_dataset_and_config_from_the_record(self, store):
        cfg = DatasetConfig.model_validate(_sales_config_doc())
        bundle = _bundle(run=_run_doc(dataset_id="ds_auto"))
        run_id = store.save_run(bundle, config=cfg)
        assert store.get_dataset("ds_auto")["source_ref"] == "/data/sales.csv"
        row = store.get_config_version("ds_auto", "3", bundle.run.config.config_hash)
        assert row is not None
        assert store.get_run(run_id)["config_hash"] == bundle.run.config.config_hash

    def test_same_fingerprint_across_runs_is_queryable(self, store):
        fp = "sha256:" + "f" * 64
        store.save_run(_bundle(run=_run_doc(run_id="run-1"), findings=[_finding(fingerprint=fp)]))
        store.save_run(_bundle(run=_run_doc(run_id="run-2"), findings=[_finding(fingerprint=fp)]))
        rows = store.runs_by_fingerprint(fp)
        assert [r["run_id"] for r in rows] == ["run-1", "run-2"]  # started_at ties → run_id order
        assert {r["severity"] for r in rows} == {"high"}

    def test_run_history_listing_is_newest_first(self, store):
        for i, started in enumerate(["2026-10-01T00:00:00Z", "2026-10-05T00:00:00Z"]):
            run = _run_doc(
                run_id=f"run-{i}",
                timing=RunTiming(started_at=started, finished_at=started, duration_ms=1),
            )
            store.save_run(_bundle(run=run))
        assert [r["run_id"] for r in store.list_runs("ds_syn")] == ["run-1", "run-0"]
        assert store.latest_run("ds_syn")["run_id"] == "run-1"

    def test_paged_check_fetch(self, store):
        bundle = _bundle(
            checks=[_check_pass(rule_id=f"r{i:02d}") for i in range(5)],
        )
        run_id = store.save_run(bundle)
        page1 = store.list_checks(run_id, limit=2)
        page2 = store.list_checks(run_id, limit=2, offset=2)
        assert [c["rule_id"] for c in page1 + page2] == ["r00", "r01", "r02", "r03"]
        assert store.list_checks(run_id, status="PASS")[0]["rule_id"] == "r00"
        assert store.list_checks(run_id, dimension="completeness") == []

    def test_schema_gate_rejects_a_bad_document(self):
        bad = {"schema_version": SCHEMA_VERSION, "run_id": "x"}  # no status/dataset/...
        with pytest.raises(MetadataIntegrityError):
            _validate("profiling_run.schema.json", bad, context="test")

    def test_finding_with_invalid_fingerprint_shape_is_refused_at_write(self, store):
        finding = _finding()
        object.__setattr__(finding, "fingerprint", "sha256:short")
        with pytest.raises(MetadataIntegrityError):
            store.save_run(_bundle(findings=[finding]))
        assert store.counts("run-syn")["findings"] == 0


# ===========================================================================
# 5. End-to-end: the real M2-T11 bundle, plus concurrency
# ===========================================================================


class TestPersistedEngineRun:
    def test_full_sales_deals_run_round_trips(self, store, sales_bundle):
        bundle, cfg = sales_bundle
        run_id = store.save_run(bundle, config=cfg)
        assert store.load_profiling_run(run_id).model_dump(mode="json") == bundle.run.model_dump(mode="json")
        loaded_checks = store.load_check_results(run_id)
        assert [c.model_dump(mode="json") for c in sorted(loaded_checks, key=lambda c: c.rule_id)] == [
            c.model_dump(mode="json") for c in sorted(bundle.checks, key=lambda c: c.rule_id)
        ]
        assert [f.model_dump(mode="json") for f in store.load_findings(run_id)] == [
            f.model_dump(mode="json") for f in bundle.findings
        ]
        assert [r.model_dump(mode="json") for r in store.load_recommendations(run_id)] == [
            r.model_dump(mode="json") for r in bundle.recommendations
        ]
        counts = store.counts(run_id)
        assert counts["findings"] == len(bundle.findings) > 0
        assert counts["run_columns"] == len(bundle.profile.columns) == 5
        # FAIL ≠ ERROR survived the real run
        tally = store.check_status_counts(run_id)
        assert set(tally) <= {"PASS", "FAIL", "WARN", "ERROR"}

    def test_persisted_profile_summary_matches_engine_rollup(self, store, sales_bundle):
        bundle, cfg = sales_bundle
        run_id = store.save_run(bundle, config=cfg)
        summary = store.profile_summary(run_id)
        profile = bundle.profile
        assert summary["row_count"] == profile.row_count.value
        assert summary["row_count_method"] == profile.row_count.provenance.method
        assert summary["column_count"] == profile.column_count
        assert summary["primary_key_candidates"] == profile.primary_key_candidates
        assert summary["grain"] == profile.grain
        if profile.temporal_coverage is not None:
            assert summary["temporal_coverage"]["column"] == profile.temporal_coverage.column
            assert summary["temporal_coverage"]["min"] == profile.temporal_coverage.min
            assert summary["temporal_coverage"]["max"] == profile.temporal_coverage.max

    def test_evidence_refs_are_queryable_rows(self, store, sales_bundle):
        bundle, cfg = sales_bundle
        run_id = store.save_run(bundle, config=cfg)
        refs = store._query(
            "SELECT DISTINCT ref FROM finding_evidence_refs WHERE run_id = ?", (run_id,)
        )
        assert refs
        finding_refs = {r for f in bundle.findings for r in f.evidence_refs}
        assert {r["ref"] for r in refs} <= finding_refs

    def test_concurrent_reader_during_writes(self, tmp_path):
        """ADR-008 validation: "concurrent read during run" — a reader store on
        the same file never sees a torn write and never blocks the writer.

        Each thread opens **its own** store: a sqlite3 connection belongs to the
        thread that created it, which is exactly the single-writer/own-handle
        discipline ADR-006/ADR-008 give the worker and the UI respectively."""
        db = tmp_path / "metadata.sqlite3"
        errors: List[BaseException] = []
        total = 12

        def write_runs() -> None:
            try:
                with MetadataStore(db) as writer:
                    for i in range(total):
                        writer.save_run(
                            _bundle(
                                run=_run_doc(run_id=f"run-{i:03d}"),
                                findings=[_finding(
                                    finding_id="F-001",
                                    fingerprint=f"sha256:{i:02d}" + "0" * 62,
                                )],
                            )
                        )
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        def read_runs() -> None:
            try:
                with MetadataStore(db) as reader:
                    seen = 0
                    # bounded so a writer that dies early fails the assert below
                    # instead of spinning forever
                    for _ in range(2000):
                        rows = reader.list_runs("ds_syn")
                        seen = len(rows)
                        for row in rows:
                            # reading a run's findings mid-commit must be consistent
                            findings = reader.load_findings(row["run_id"])
                            assert len(findings) <= 1
                        if seen >= total:
                            break
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=write_runs), threading.Thread(target=read_runs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
            assert not t.is_alive()
        assert errors == []
        with MetadataStore(db) as check:
            assert len(check.list_runs("ds_syn")) == total

    def test_using_a_closed_store_is_refused(self, tmp_path):
        db = tmp_path / "metadata.sqlite3"
        st = MetadataStore(db)
        st.close()
        with pytest.raises(MetadataStoreError):
            st.list_runs()


# ===========================================================================
# 6. Normalised (run_id, column, metric) rows — M3-T3, cheap run-history diffs
# ===========================================================================


def _metric_columns(mean_value=18450, missing_rate=0.15):
    """Two §3.2 columns that carry every promoted-metric shape: an exact stat,
    an approximate stat (algorithm), a sampled metric (seed), a ``not_computed``
    metric (absent value + reason) and a distribution metric — plus a second
    column whose only metric is a uniqueness distinct-count."""
    return [
        ColumnProfile(
            schema_version=SCHEMA_VERSION, column="deal_value", ordinal=0,
            physical_type="VARCHAR", logical_type="numeric_currency", semantic_type="measure",
            missingness=Missingness(
                null_count=3000, blank_count=0, sentinel_count=0,
                missing_rate=MetricValue(
                    value=missing_rate, denominator=20000,
                    provenance=Provenance(
                        method="sampled",
                        method_params={
                            "sampling_strategy": "reservoir",
                            "sample_size": 100000,
                            "seed": 20261024,
                        },
                    ),
                ),
            ),
            stats=NumericStats(
                valid_count=17000,
                mean=MetricValue(value=mean_value, provenance=Provenance(method="exact")),
                p95=MetricValue(
                    value=82000,
                    provenance=Provenance(
                        method="approximate",
                        method_params={"algorithm": "duckdb_approx_quantile", "confidence": 0.95},
                    ),
                ),
                median=MetricValue(
                    provenance=Provenance(method="not_computed", reason="column_type_unsupported")
                ),
            ),
            distribution=Distribution(
                skewness=MetricValue(value=4.2, provenance=Provenance(method="exact"))
            ),
        ),
        ColumnProfile(
            schema_version=SCHEMA_VERSION, column="deal_id", ordinal=1,
            physical_type="VARCHAR", logical_type="identifier", semantic_type="identifier",
            uniqueness=Uniqueness(
                distinct_count=MetricValue(
                    value=188,
                    provenance=Provenance(
                        method="approximate",
                        method_params={"algorithm": "duckdb_count_distinct_hll"},
                    ),
                ),
                is_unique_candidate=False,
            ),
        ),
    ]


def _metric_bundle(run_id="run-syn", dataset_id="ds_syn", *, started_at="2026-10-24T09:00:00Z", **col_kw):
    return _bundle(
        run=_run_doc(
            run_id=run_id, dataset_id=dataset_id,
            timing=RunTiming(started_at=started_at, finished_at=started_at, duration_ms=1000),
        ),
        profile=_profile_doc(run_id=run_id, column_count=2, columns=_metric_columns(**col_kw)),
    )


class TestColumnMetricValues:
    def test_every_metricvalue_leaf_is_promoted(self, store):
        run_id = store.save_run(_metric_bundle())
        rows = store.column_metrics(run_id)
        # deal_value: missingness.missing_rate + stats.mean/p95/median + distribution.skewness
        # deal_id:    uniqueness.distinct_count  ==  6 addressed cells
        assert {(r["column_name"], r["metric_name"]) for r in rows} == {
            ("deal_value", "missingness.missing_rate"),
            ("deal_value", "stats.mean"),
            ("deal_value", "stats.p95"),
            ("deal_value", "stats.median"),
            ("deal_value", "distribution.skewness"),
            ("deal_id", "uniqueness.distinct_count"),
        }
        assert store.counts(run_id)["column_metric_values"] == len(_iter_column_metrics(_metric_columns()[0]) + _iter_column_metrics(_metric_columns()[1])) == 6

    def test_reads_a_single_cell_without_the_artifact(self, store):
        run_id = store.save_run(_metric_bundle())
        mv = store.metric_value(run_id, "deal_value", "stats.mean")
        assert mv is not None and mv.value == 18450
        assert mv.provenance.method == "exact"

    def test_absent_cell_reads_back_as_none_not_zero(self, store):
        run_id = store.save_run(_metric_bundle())
        # deal_id has no stats block at all — a diff must see "no cell", not a 0
        assert store.metric_value(run_id, "deal_id", "stats.mean") is None

    def test_sampled_seed_and_denominator_survive_the_round_trip(self, store):
        run_id = store.save_run(_metric_bundle())
        src = _metric_columns()[0].missingness.missing_rate
        mv = store.metric_value(run_id, "deal_value", "missingness.missing_rate")
        assert mv.provenance.method == "sampled"
        assert mv.provenance.method_params["seed"] == 20261024  # ADR-015 reproducibility lock
        assert mv.denominator == 20000
        assert mv.model_dump(mode="json") == src.model_dump(mode="json")

    def test_not_computed_keeps_value_absent_not_null(self, store):
        run_id = store.save_run(_metric_bundle())
        mv = store.metric_value(run_id, "deal_value", "stats.median")
        assert mv.provenance.method == "not_computed"
        assert mv.provenance.reason == "column_type_unsupported"
        # §1: not_computed must NOT carry a value (a null would masquerade as data)
        assert "value" not in mv.model_fields_set
        # and the source it projects from agrees
        assert mv.model_dump(mode="json") == _metric_columns()[0].stats.median.model_dump(mode="json")

    def test_all_promoted_cells_equal_their_artifact_source(self, store):
        bundle = _metric_bundle()
        run_id = store.save_run(bundle)
        for column in bundle.profile.columns:
            for name, src in _iter_column_metrics(column):
                mv = store.metric_value(run_id, column.column, name)
                assert mv is not None and mv.model_dump(mode="json") == src.model_dump(mode="json")

    def test_metric_history_returns_newest_first_for_diffing(self, store):
        store.save_run(_metric_bundle(run_id="run-old", started_at="2026-10-01T00:00:00Z", mean_value=17000))
        store.save_run(_metric_bundle(run_id="run-new", started_at="2026-10-02T00:00:00Z", mean_value=19000))
        hist = store.metric_history("ds_syn", "deal_value", "stats.mean")
        assert [h["run_id"] for h in hist] == ["run-new", "run-old"]
        assert [json.loads(h["value_json"]) for h in hist] == [19000, 17000]
        assert all(h["method"] == "exact" for h in hist)
        assert [h["run_id"] for h in store.metric_history("ds_syn", "deal_value", "stats.mean", limit=1)] == ["run-new"]

    def test_metric_rows_cascade_when_the_run_is_deleted(self, store):
        run_id = store.save_run(_metric_bundle())
        assert store.counts(run_id)["column_metric_values"] == 6
        with store._write_tx() as conn:
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        assert store._query(
            "SELECT 1 FROM column_metric_values WHERE run_id = ?", (run_id,)
        ) == []

    def test_real_engine_run_promotes_its_column_metrics(self, store, sales_bundle):
        bundle, cfg = sales_bundle
        run_id = store.save_run(bundle, config=cfg)
        total = 0
        for column in bundle.profile.columns:
            for name, src in _iter_column_metrics(column):
                total += 1
                mv = store.metric_value(run_id, column.column, name)
                assert mv is not None
                assert mv.model_dump(mode="json") == src.model_dump(mode="json")
        assert total > 0  # the real run actually carries promotable metrics
        assert store.counts(run_id)["column_metric_values"] == total
