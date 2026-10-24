"""M3-T2 tests — artifact file store: layout, lifecycle, cleanup (ADR-008;
SECURITY §2/§4/§6.5; CONTRACTS §6.3).

The metadata half (M3-T1) already stores ``run_artifacts`` **rows** and the
store-relative ``ref_path`` convention (``artifacts/<run_id>/<file>``). These
tests cover the **file** half those rows point at, in the order the specs frame
them:

1. **Layout** — a write lands under ``<data_root>/artifacts/<run_id>/`` and the
   returned ref is store-relative (never absolute), matching the CONTRACTS §2
   ``result_refs`` example so the row and the file agree.
2. **Confinement / safety** — a ref is resolved only inside the store; ``..`` /
   absolute escape → ``SOURCE_NOT_ALLOWED``; the root obeys the app-bundle
   separation policy; IO failure → ``ARTIFACT_WRITE_FAILED`` (never a raw
   ``OSError`` past the bridge).
3. **Lifecycle** — retention categories per kind; staging lives in the temp
   workspace and is cleaned after the run; a written document is deterministic.
4. **Cleanup** — the explicit-delete paths (run rows, whole dataset) and the
   crash/partial-artifact orphan sweep, all driven through the *metadata rows*
   as the authority for what is live.
5. **The join** — one real ``profile_dataset`` run written through the store and
   persisted via ``MetadataStore.save_run``: every ``list_artifacts`` row
   resolves back to an existing file, and deleting/sweeping removes exactly the
   referenced bytes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.orchestration.rollup import profile_dataset
from data_profiling_engine.schemas.models import DatasetConfig
from data_profiling_engine.storage.artifact_store import (
    STORE_PREFIX,
    ArtifactStore,
    ArtifactStoreError,
    RetentionPolicy,
    retention_for,
)
from data_profiling_engine.storage.metadata_store import MetadataStore
from data_profiling_engine.storage.paths import (
    DirectoryPolicyError,
    artifacts_dir,
)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _config_doc() -> dict:
    return {
        "config_version": 1,
        "dataset": {"name": "tiny", "grain": "one_row_per_item", "primary_key": ["id"]},
        "profiling": {"mode": "standard"},
        "quality_checks": [
            {"id": "id_unique", "rule": "unique", "column": "id",
             "dimension": "uniqueness", "severity": "critical"},
        ],
    }


def _write_tiny_csv(csv: Path, n: int = 5) -> None:
    lines = ["id,val"]
    for i in range(n):
        lines.append(f"{i},{i * 10}")
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    """A user-data root kept outside the app bundle (the store resolves
    artifacts under ``<data_root>/artifacts``)."""
    root = tmp_path / "userdata"
    root.mkdir()
    return root


@pytest.fixture()
def store(data_root: Path) -> ArtifactStore:
    return ArtifactStore(
        data_root=data_root,
        staging_root=data_root.parent / "tmp",
        cache_root=data_root.parent / "cache",
    )


@pytest.fixture()
def meta(tmp_path: Path) -> MetadataStore:
    st = MetadataStore(tmp_path / "meta" / "metadata.sqlite3")
    try:
        yield st
    finally:
        st.close()


# ---------------------------------------------------------------------------
# 1. layout
# ---------------------------------------------------------------------------


class TestLayout:
    def test_ref_is_store_relative_and_matches_contracts_shape(self, store, data_root):
        ref = store.write_text("run-1", "profile_json", "{}")
        # artifacts/<run_id>/<file>, relative to the data root, never absolute
        assert ref == f"{STORE_PREFIX}/run-1/profile.json"
        assert not Path(ref).is_absolute()
        # the file physically lives at <data_root>/artifacts/run-1/profile.json
        assert (data_root / ref).is_file()
        assert (data_root / ref).read_text(encoding="utf-8") == "{}"

    def test_canonical_filenames_per_kind(self, store):
        assert store.ref_path("r", "profile_json") == "artifacts/r/profile.json"
        assert store.ref_path("r", "html_report") == "artifacts/r/report.html"
        assert store.ref_path("r", "issue_csv") == "artifacts/r/issues.csv"
        # unknown kind defaults to <kind>.json so a new artifact needs no code change
        assert store.ref_path("r", "custom_blob") == "artifacts/r/custom_blob.json"

    def test_write_creates_private_run_directory(self, store, data_root):
        store.write_text("run-9", "profile_json", "hi")
        run_dir = data_root / "artifacts" / "run-9"
        assert run_dir.is_dir()
        # SECURITY §4 file-access scope: user-only on POSIX
        import os
        import stat
        if os.name != "nt":
            assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700

    def test_explicit_filename_override(self, store):
        ref = store.write_text("run-x", "profile_json", "data", filename="alt.json")
        assert ref == "artifacts/run-x/alt.json"
        assert (Path(store.artifacts_root) / "run-x" / "alt.json").is_file()

    def test_store_root_resolves_under_data_root(self, store, data_root):
        assert store.artifacts_root == artifacts_dir(data_root)
        assert store.data_root == data_root.resolve()


# ---------------------------------------------------------------------------
# 2. confinement / safety
# ---------------------------------------------------------------------------


class TestConfinement:
    def test_relative_ref_resolves_inside_store(self, store):
        abs_path = store.resolve("artifacts/run-1/profile.json")
        assert store.artifacts_root in abs_path.parents

    def test_traversal_escape_is_refused(self, store):
        with pytest.raises(ArtifactStoreError) as exc:
            store.resolve("artifacts/../../etc/passwd")
        assert exc.value.code == ErrorCode.SOURCE_NOT_ALLOWED

    def test_absolute_outside_path_is_refused(self, store):
        outside = store.data_root.parent / "somewhere-else.json"
        with pytest.raises(ArtifactStoreError) as exc:
            store.resolve(outside)
        assert exc.value.code == ErrorCode.SOURCE_NOT_ALLOWED

    def test_filename_with_separator_is_refused(self, store):
        with pytest.raises(ArtifactStoreError) as exc:
            store.write_text("run-1", "profile_json", "x", filename="a/b.json")
        assert exc.value.code == ErrorCode.SOURCE_NOT_ALLOWED

    def test_run_id_with_separator_is_refused(self, store):
        with pytest.raises(ArtifactStoreError) as exc:
            store.write_text("../escape", "profile_json", "x")
        assert exc.value.code == ErrorCode.SOURCE_NOT_ALLOWED

    def test_data_root_inside_app_bundle_is_refused(self, monkeypatch):
        # SECURITY §2/§6.5: the root cannot be inside the frozen bundle.
        import sys

        bundle = Path(sys.prefix).resolve()
        monkeypatch.setattr(
            "data_profiling_engine.storage.paths.app_bundle_dir", lambda: bundle,
        )
        with pytest.raises(DirectoryPolicyError):
            ArtifactStore(data_root=bundle / "DataProfilingEngine")

    def test_open_missing_artifact_raises_write_failed_code(self, store):
        with pytest.raises(ArtifactStoreError) as exc:
            store.open_path("artifacts/ghost/profile.json")
        assert exc.value.code == ErrorCode.ARTIFACT_WRITE_FAILED

    def test_read_back_roundtrip(self, store):
        ref = store.write_text("run-2", "html_report", "<html>x</html>")
        assert store.read_text(ref) == "<html>x</html>"
        assert store.exists(ref) is True
        assert store.open_path(ref).is_file()


# ---------------------------------------------------------------------------
# 3. lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_retention_categories(self):
        assert retention_for("profile_json") is RetentionPolicy.RESULT
        assert retention_for("report_export") is RetentionPolicy.EXPORT
        # unknown kind defaults to the conservative RESULT (keep until told)
        assert retention_for("some_new_kind") is RetentionPolicy.RESULT

    def test_write_document_is_deterministic(self, store):
        doc = {"b": 2, "a": 1, "nested": {"y": [1, 2], "x": None}}
        ref1 = store.write_document("run-d", "profile_json", doc)
        ref2 = store.write_document("run-d2", "profile_json", doc, filename="profile.json")
        text = store.read_text(ref1)
        assert text == store.read_text(ref2)
        assert text.endswith("\n")
        assert json.loads(text) == doc
        # sort_keys: keys appear in sorted order in the raw text
        assert text.index('"a"') < text.index('"b"') < text.index('"nested"')

    def test_stage_cleans_up_after_context(self, store):
        with store.stage("run-s") as staging_dir:
            staged = staging_dir / "scratch.parquet"
            staged.write_bytes(b"raw")
            assert staged.is_file()
        # SECURITY §4: staging files cleaned after run, even on success
        assert not staging_dir.exists()

    def test_stage_cleans_up_on_exception(self, store):
        with pytest.raises(RuntimeError):
            with store.stage("run-crash"):
                raise RuntimeError("boom")
        assert not (store.staging_root / "run-crash").exists()


# ---------------------------------------------------------------------------
# 4. cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_delete_run_artifacts_removes_referenced_files(self, store):
        ref_a = store.write_text("run-a", "profile_json", "1")
        ref_b = store.write_text("run-a", "html_report", "2")
        rows = [{"ref_path": ref_a}, {"ref_path": ref_b}]
        removed = store.delete_run_artifacts(rows)
        assert removed == 2
        assert not store.exists(ref_a) and not store.exists(ref_b)
        # run dir pruned once empty
        assert not (store.artifacts_root / "run-a").exists()

    def test_delete_run_artifacts_is_idempotent(self, store):
        ref = store.write_text("run-i", "profile_json", "x")
        assert store.delete_run_artifacts([{"ref_path": ref}]) == 1
        # second pass over the same rows finds nothing, removes nothing
        assert store.delete_run_artifacts([{"ref_path": ref}]) == 0

    def test_delete_run_artifacts_skips_escaping_refs(self, store):
        # a corrupt ref outside the store must never be touched
        outside = store.data_root.parent / "keep-me.txt"
        outside.write_text("precious", encoding="utf-8")
        removed = store.delete_run_artifacts([{"ref_path": "../keep-me.txt"}])
        assert removed == 0
        assert outside.is_file()

    def test_clear_staging_removes_entries_keeps_root(self, store):
        (store.staging_root).mkdir(parents=True, exist_ok=True)
        s = store.staging_root / "leftover"
        s.mkdir()
        (s / "f.tmp").write_text("x", encoding="utf-8")
        removed = store.clear_staging()
        assert removed == 1
        assert store.staging_root.is_dir()
        assert not s.exists()

    def test_clear_cache_removes_rebuildables(self, store):
        store.cache_root.mkdir(parents=True, exist_ok=True)
        (store.cache_root / "a.tmp").write_text("x", encoding="utf-8")
        assert store.clear_cache() == 1
        assert store.cache_root.is_dir()
        assert not any(store.cache_root.iterdir())


# ---------------------------------------------------------------------------
# 5. the join with the metadata store (real run)
# ---------------------------------------------------------------------------


class TestMetadataJoin:
    @pytest.fixture()
    def real_bundle(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        csv = ws / "tiny.csv"
        _write_tiny_csv(csv)
        cfg = DatasetConfig.model_validate(_config_doc())
        return profile_dataset("tiny", csv, cfg, "run-join", allowed_roots=[ws])

    def test_save_run_rows_resolve_to_files_written_by_the_store(
        self, store, meta, real_bundle
    ):
        # Write the canonical result through the file store, then persist the
        # run citing that ref (ADR-008: "large results live as files,
        # referenced by rows"). The row's ref and the file must agree.
        from data_profiling_engine.reporting import result_document

        ref = store.write_document("run-join", "profile_json", result_document(real_bundle))
        run_id = meta.save_run(real_bundle, artifact_refs={"profile_json": ref})

        rows = meta.list_artifacts(run_id)
        assert any(r["ref_path"] == ref for r in rows)
        for row in rows:
            assert store.exists(row["ref_path"])  # every row points at a real file
        assert store.read_text(ref).startswith("{")

    def test_sweep_orphans_removes_files_with_no_row(self, store, meta, real_bundle):
        from data_profiling_engine.reporting import result_document

        # A durable artifact with a row ...
        kept = store.write_document("run-join", "profile_json", result_document(real_bundle))
        meta.save_run(real_bundle, artifact_refs={"profile_json": kept})
        # ... and an orphan a crashed run left with no row at all.
        orphan = store.write_text("run-crashed", "profile_json", "{}")
        assert meta.all_artifact_refs()  # sanity: rows exist

        removed = store.sweep_orphans(meta)
        assert orphan in removed
        assert kept not in removed
        assert store.exists(kept)
        assert not store.exists(orphan)

    def test_delete_dataset_artifacts_removes_all_runs(self, store, meta, real_bundle):
        ref = store.write_text("run-join", "profile_json", "x")
        meta.save_run(real_bundle, artifact_refs={"profile_json": ref})
        removed = store.delete_dataset_artifacts(meta, "tiny")
        assert removed >= 1
        assert not store.exists(ref)

    def test_save_run_with_result_ref_from_bundle(self, store, meta, real_bundle):
        """A bundle whose ``result_refs`` already cite a store-relative path
        (the CLI shape) round-trips through the same join point."""
        ref = store.write_text("run-join", "profile_json", "content")
        # result_refs on the run object are picked up by save_run directly
        run_with_ref = real_bundle.run.model_copy(
            update={"result_refs": {"profile_json": ref}}
        )
        from data_profiling_engine.orchestration.rollup import ProfileBundle

        bundle = ProfileBundle(
            run=run_with_ref,
            profile=real_bundle.profile,
            checks=real_bundle.checks,
            findings=real_bundle.findings,
            recommendations=real_bundle.recommendations,
            errors=real_bundle.errors,
        )
        run_id = meta.save_run(bundle)
        assert meta.artifact_ref(run_id, "profile_json") == ref
        assert store.read_text(ref) == "content"
