"""File artifact store — layout, lifecycle policy, cleanup (M3-T2).

ADR-008 splits persistence in two: the SQLite **metadata store** (M3-T1) owns
registry/runs/jobs/findings *rows* and **artifact references**, while "large
results live in the artifact store as files, referenced by rows". This module
is the FILE half of that contract — the directory layout those references point
into, the retention policy that says how long each class of file survives, and
the cleanup entry points SECURITY §4 / §6.5 and CONTRACTS §6.3 require.

The join with the metadata side is deliberate and narrow: a ``run_artifacts``
row's ``ref_path`` is **store-relative, never absolute** ("so moving the
user-data directory keeps the reference resolvable" — migrations v1), and this
store resolves exactly those ``artifacts/<run_id>/<file>`` refs. Nothing here
restructures a reference or writes a metadata row; it only turns a reference
into bytes on disk and back.

Directory kinds and their lifecycle come straight from the SECURITY §2 table:

===================  =========================================  =================
kind                 location                                    upgrade behavior
===================  =========================================  =================
run result / export  ``<data_root>/artifacts/<run_id>/<file>``  preserved; removed
                     (this store)                               only by explicit
                                                               user action
staging              ``<temp_root>/<run_id>/``                  cleaned after run
cache                ``<cache_root>``                           may be cleared
===================  =========================================  =================

Two security guarantees are enforced here rather than trusted to callers
(SECURITY §3/§4, AGENTS §7):

* **Store confinement** — every ref resolves to a real path *inside* the
  artifact root; a ``..``/absolute escape is refused with
  :class:`~data_profiling_engine.errors.ErrorCode.SOURCE_NOT_ALLOWED`, the same
  out-of-scope code the bridge path allow-list uses. ``artifact.open`` can only
  ever reach a file this store owns.
* **Bundle separation** — the root comes from :mod:`storage.paths`
  (``artifacts_dir`` → ``resolve_data_root``), which already refuses a user-data
  path inside the frozen app bundle and creates the directory user-only
  (``0o700``); an artifact written here therefore survives an upgrade and is
  not world-readable.
"""
from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, Iterator, List, Optional

from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.storage.paths import (
    ARTIFACT_SUBDIR,
    DEFAULT_APP_NAME,
    artifacts_dir,
    default_cache_root,
    default_temp_root,
    ensure_private_dir,
    resolve_data_root,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps storage engine-agnostic
    from data_profiling_engine.storage.metadata_store import MetadataStore

#: Store-relative prefix of every run-artifact reference. A ``run_artifacts``
#: ref_path is ``artifacts/<run_id>/<file>`` (relative to the user-data root,
#: matching the CONTRACTS §2 ``result_refs`` example ``artifacts/DP-0001/
#: profile.json``); :data:`ARTIFACT_SUBDIR` is the single source of the
#: ``artifacts`` segment so the two layers can never drift.
STORE_PREFIX = ARTIFACT_SUBDIR

#: Canonical file name per well-known artifact kind (CONTRACTS §2 ``result_refs``
#: keys → the on-disk name inside the run directory). A kind absent from this
#: map gets a ``<kind>.json`` name, so an M4 report kind or a custom artifact
#: still lands somewhere predictable without a second code change here.
DEFAULT_FILENAMES: Dict[str, str] = {
    "profile_json": "profile.json",
    "html_report": "report.html",
    "issue_csv": "issues.csv",
}


class RetentionPolicy(str, Enum):
    """How long a class of artifact file survives (SECURITY §2 lifecycle)."""

    RESULT = "result"        # run output; survives upgrade; explicit-delete only
    EXPORT = "export"        # user-initiated export; NEVER auto-deleted
    STAGING = "staging"      # in-progress; cleaned after the run ends
    REBUILDABLE = "rebuild"  # cache; may be cleared at any time


#: Retention attached to each run-artifact kind. The run store only ever holds
#: RESULT/EXPORT files (both survive upgrade); STAGING/REBUILDABLE live in the
#: temp/cache roots and are reached through :meth:`ArtifactStore.stage` /
#: :meth:`ArtifactStore.clear_cache`, never mixed into ``run_artifacts`` refs.
#: An unknown kind defaults to RESULT — the conservative reading (keep it,
#: delete only on request) is the one SECURITY §4 implies for a result file.
KIND_RETENTION: Dict[str, RetentionPolicy] = {
    "profile_json": RetentionPolicy.RESULT,
    "html_report": RetentionPolicy.RESULT,
    "issue_csv": RetentionPolicy.RESULT,
    "run_export": RetentionPolicy.EXPORT,
    "report_export": RetentionPolicy.EXPORT,
}


def retention_for(kind: str) -> RetentionPolicy:
    """Retention policy for an artifact ``kind`` (default :attr:`RESULT`)."""
    return KIND_RETENTION.get(kind, RetentionPolicy.RESULT)


class ArtifactStoreError(RuntimeError):
    """Artifact-file failure. ``code`` is a CONTRACTS §8 registry code so the
    bridge/``artifact.open`` and job events surface it consistently; the message
    never carries raw data values or credentials (SECURITY §5/§7)."""

    def __init__(
        self, message: str, *, code: ErrorCode = ErrorCode.ARTIFACT_WRITE_FAILED
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _dump_deterministic(document: Any) -> str:
    """Canonical JSON text for a result document (``sort_keys=True``, trailing
    newline) — byte-for-byte the same rule ``reporting.write_profile_json``
    applies, so a document written through the store is golden-snapshot stable
    (TESTING §3.3) and identical to the CLI's ``--out`` output."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _safe_basename(filename: str) -> str:
    """Validate a caller-supplied file *name*: it must be a single path segment
    with no directory component or traversal, so a ref can never be built that
    escapes the run directory."""
    name = filename.strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or os.path.isabs(name):
        raise ArtifactStoreError(
            f"artifact filename must be a bare file name, not {filename!r}",
            code=ErrorCode.SOURCE_NOT_ALLOWED,
        )
    return name


class ArtifactStore:
    """The file side of ADR-008: a confined, per-run artifact layout under
    ``<data_root>/artifacts`` plus its lifecycle and cleanup policy.

    Constructed against the same user-data root the :class:`MetadataStore`
    resolved (SECURITY §2), so a ``run_artifacts`` row written by one and a
    file written by the other always refer to the same physical store. The
    directory is created lazily and user-only; nothing is written until a call
    asks for it."""

    def __init__(
        self,
        *,
        data_root: "Path | str | None" = None,
        app_name: str = DEFAULT_APP_NAME,
        staging_root: "Path | str | None" = None,
        cache_root: "Path | str | None" = None,
    ) -> None:
        """``data_root`` redirects the OS-standard user-data resolution (tests,
        per-user packaging) exactly as :class:`MetadataStore` does; the artifact
        root is then ``<data_root>/artifacts``. ``staging_root`` /
        ``cache_root`` override the temp/rebuildable locations for a test so no
        call has to touch the real ``TMPDIR``."""
        self._data_root = resolve_data_root(data_root, app_name=app_name)
        self._app_name = app_name
        self._artifacts_root = artifacts_dir(self._data_root, app_name=app_name)
        self._staging_root = (
            Path(staging_root).expanduser().resolve()
            if staging_root is not None
            else default_temp_root(app_name=app_name)
        )
        self._cache_root = (
            Path(cache_root).expanduser().resolve()
            if cache_root is not None
            else default_cache_root(app_name=app_name)
        )

    # -- resolution ---------------------------------------------------------

    @property
    def data_root(self) -> Path:
        """The user-data directory the store-relative refs are relative to."""
        return self._data_root

    @property
    def artifacts_root(self) -> Path:
        """Absolute root of the run-artifact file store."""
        return self._artifacts_root

    @property
    def staging_root(self) -> Path:
        """Temporary workspace root (SECURITY §2: cleaned by policy)."""
        return self._staging_root

    @property
    def cache_root(self) -> Path:
        """Rebuildable cache root (SECURITY §2: may be cleared at any time)."""
        return self._cache_root

    def run_dir(self, run_id: str) -> Path:
        """Absolute per-run directory; created (user-only) on demand."""
        return self._artifacts_root / _safe_basename(run_id)

    def ref_path(self, run_id: str, kind: str, *, filename: Optional[str] = None) -> str:
        """Store-relative reference a ``run_artifacts`` row stores for one
        artifact: ``artifacts/<run_id>/<file>``. This is the *only* form that
        crosses into the metadata side, and it is always relative (never
        absolute) so a relocated user-data directory keeps resolving (§2)."""
        name = _safe_basename(filename) if filename else DEFAULT_FILENAMES.get(kind, f"{kind}.json")
        return f"{STORE_PREFIX}/{_safe_basename(run_id)}/{name}"

    def resolve(self, ref: "Path | str") -> Path:
        """Turn a store-relative ref (or an absolute path inside the store) into
        a validated absolute path.

        The result is guaranteed to live under :attr:`artifacts_root`; a
        reference that tries to escape via ``..`` or an absolute outside path is
        refused with :class:`~data_profiling_engine.errors.ErrorCode.SOURCE_NOT_ALLOWED`
        (SECURITY §3/§4 path allow-list — ``artifact.open`` can reach nothing the
        store does not own)."""
        candidate = Path(ref)
        if candidate.is_absolute():
            abs_path = candidate
        else:
            abs_path = self._data_root / candidate
        # lexists() (not exists()) so a dangling symlink still resolves; then
        # confine on the *real* path to defeat symlink/.. tricks.
        try:
            real = abs_path.resolve()
        except OSError as exc:  # pragma: no cover - exotic filesystem errors
            raise ArtifactStoreError(
                f"cannot resolve artifact ref {ref!r}: {exc}",
                code=ErrorCode.SOURCE_NOT_ALLOWED,
            ) from exc
        root = self._artifacts_root
        if real != root and root not in real.parents:
            raise ArtifactStoreError(
                f"artifact ref {ref!r} resolves outside the artifact store {root}",
                code=ErrorCode.SOURCE_NOT_ALLOWED,
            )
        return real

    # -- writing ------------------------------------------------------------

    def _ensure_run_dir(self, run_id: str) -> Path:
        directory = self.run_dir(run_id)
        try:
            return ensure_private_dir(directory)
        except OSError as exc:
            raise ArtifactStoreError(
                f"could not create artifact directory for run {run_id!r}: {exc}",
                code=ErrorCode.ARTIFACT_WRITE_FAILED,
            ) from exc

    def write_text(
        self, run_id: str, kind: str, content: str, *, filename: Optional[str] = None
    ) -> str:
        """Write ``content`` (UTF-8) as a run artifact and return its
        store-relative ref path — the value to hand to
        ``MetadataStore.save_run(artifact_refs={kind: <this>})`` so the row and
        the file agree. IO failures surface as
        :class:`~data_profiling_engine.errors.ErrorCode.ARTIFACT_WRITE_FAILED`,
        never a raw ``OSError`` leaking past the bridge."""
        path = self._ensure_run_dir(run_id) / (
            _safe_basename(filename) if filename else DEFAULT_FILENAMES.get(kind, f"{kind}.json")
        )
        try:
            # newline="\n": artifacts must be byte-stable across OSes (TESTING §3.3
            # golden snapshots); the default text mode would emit CRLF on Windows.
            path.write_text(content, encoding="utf-8", newline="\n")
        except OSError as exc:
            raise ArtifactStoreError(
                f"could not write artifact {kind!r} for run {run_id!r}: {exc}",
                code=ErrorCode.ARTIFACT_WRITE_FAILED,
            ) from exc
        return self.ref_path(run_id, kind, filename=path.name)

    def write_bytes(
        self, run_id: str, kind: str, data: bytes, *, filename: Optional[str] = None
    ) -> str:
        """Binary sibling of :meth:`write_text` (a self-contained HTML/plot
        export or a compressed artifact)."""
        path = self._ensure_run_dir(run_id) / (
            _safe_basename(filename) if filename else DEFAULT_FILENAMES.get(kind, f"{kind}.json")
        )
        try:
            path.write_bytes(data)
        except OSError as exc:
            raise ArtifactStoreError(
                f"could not write artifact {kind!r} for run {run_id!r}: {exc}",
                code=ErrorCode.ARTIFACT_WRITE_FAILED,
            ) from exc
        return self.ref_path(run_id, kind, filename=path.name)

    def write_document(
        self, run_id: str, kind: str, document: Any, *, filename: Optional[str] = None
    ) -> str:
        """Write a contract/JSON **document** as deterministic JSON
        (``sort_keys``, trailing newline) — the canonical result file, byte-stable
        for golden snapshots (TESTING §3.3)."""
        return self.write_text(
            run_id, kind, _dump_deterministic(document), filename=filename or "profile.json"
        )

    # -- reading / artifact.open --------------------------------------------

    def exists(self, ref: "Path | str") -> bool:
        """Whether the ref points at a real file inside the store."""
        try:
            return self.resolve(ref).is_file()
        except ArtifactStoreError:
            return False

    def read_bytes(self, ref: "Path | str") -> bytes:
        path = self.resolve(ref)
        if not path.is_file():
            raise ArtifactStoreError(
                f"artifact {str(ref)!r} not found", code=ErrorCode.ARTIFACT_WRITE_FAILED
            )
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ArtifactStoreError(
                f"could not read artifact {str(ref)!r}: {exc}",
                code=ErrorCode.ARTIFACT_WRITE_FAILED,
            ) from exc

    def read_text(self, ref: "Path | str") -> str:
        return self.read_bytes(ref).decode("utf-8")

    def open_path(self, ref: "Path | str") -> Path:
        """The ``artifact.open`` entry point (SECURITY §3): return the validated
        absolute path of an allow-listed artifact so the host can reveal it in
        the OS file viewer — never a caller-supplied arbitrary path."""
        path = self.resolve(ref)
        if not path.is_file():
            raise ArtifactStoreError(
                f"artifact {str(ref)!r} not found", code=ErrorCode.ARTIFACT_WRITE_FAILED
            )
        return path

    # -- staging (temp workspace) -------------------------------------------

    @contextmanager
    def stage(self, run_id: str) -> Iterator[Path]:
        """A per-run temporary workspace (SECURITY §2 "staging, in-progress
        artifacts"; §4 "staging files cleaned after run").

        Yields the created staging directory; it is removed on exit whether the
        body succeeded or raised, so a crash inside the worker cannot leave raw
        staged copies hoarded on disk. Nothing durable lives here — a caller that
        wants a surviving result writes through :meth:`write_text`, not
        :meth:`stage`."""
        directory = self._staging_root / _safe_basename(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            yield directory
        finally:
            self._rmtree(directory)

    # -- cleanup -------------------------------------------------------------

    def _rmtree(self, directory: Path) -> None:
        """Remove a directory tree, ignoring absence (idempotent cleanup)."""
        shutil.rmtree(directory, ignore_errors=True)

    def delete_run_artifacts(self, rows: Iterable[Dict[str, Any]]) -> int:
        """Delete the FILES referenced by a run's ``run_artifacts`` rows and
        prune the run directory once it is empty.

        This is the explicit-delete path (SECURITY §4 "delete this dataset's
        history and artifacts", §6.1 CANCELLED "partial artifacts", §6.3
        "temp-artifact cleanup"): it is only ever driven by a user action or a
        terminal job event, never by an upgrade or a background sweep of RESULT
        files. ``rows`` is whatever ``MetadataStore.list_artifacts(run_id)``
        returns; a ref whose file is already gone is skipped, so the operation is
        idempotent. Returns the number of files actually removed. Only removes
        files inside the store (:meth:`resolve` confines each ref)."""
        removed = 0
        run_ids = set()
        for row in rows:
            ref = row.get("ref_path")
            if not ref:
                continue
            try:
                path = self.resolve(ref)
            except ArtifactStoreError:
                continue  # ref escapes the store — never touch it here
            if path.is_file():
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    raise ArtifactStoreError(
                        f"could not delete artifact {str(ref)!r}: {exc}",
                        code=ErrorCode.ARTIFACT_WRITE_FAILED,
                    ) from exc
            run_dir = path.parent
            if run_dir != self._artifacts_root:
                run_ids.add(run_dir)
        for run_dir in run_ids:
            if run_dir.is_dir() and not any(run_dir.iterdir()):
                try:
                    run_dir.rmdir()
                except OSError:  # pragma: no cover - raced with a concurrent write
                    pass
        return removed

    def delete_dataset_artifacts(self, store: "MetadataStore", dataset_id: str) -> int:
        """Delete every artifact file for every run of one dataset (the
        dataset-scoped "delete this dataset's history and artifacts" action).

        The metadata rows are the authority for *which* files belong to the
        dataset, so runs are read through ``store.list_runs(dataset_id)`` and
        their refs through ``store.list_artifacts(run_id)`` — this only removes
        the FILES (removing the rows themselves is the metadata store's job, in
        the same explicit user action, M5). Returns the number of files removed."""
        removed = 0
        for run in store.list_runs(dataset_id):
            removed += self.delete_run_artifacts(store.list_artifacts(run["run_id"]))
        return removed

    def sweep_orphans(self, store: "MetadataStore") -> List[str]:
        """Delete files under the run layout that **no** ``run_artifacts`` row
        references, returning the store-relative refs removed.

        This is the safety net for the crash/partial-artifact case (ADR-006
        "worker crash does not corrupt the store", CONTRACTS §6.1 "partial
        artifacts marked", §6.3 "temp-artifact cleanup"): a run that died after
        writing files but before its single ``save_run`` transaction committed
        leaves bytes with no owning row. Those, and only those, are removed —
        a file with a live reference is a RESULT/EXPORT artifact that survives
        until an explicit delete, so it is never swept. Only ``<run_id>/``
        sub-directories directly under the artifact root are considered, so a
        user export written elsewhere is out of scope by construction."""
        referenced = {row["ref_path"] for row in store.all_artifact_refs()}
        removed: List[str] = []
        if not self._artifacts_root.is_dir():
            return removed
        for run_dir in sorted(self._artifacts_root.iterdir()):
            if not run_dir.is_dir():
                continue
            for path in sorted(run_dir.rglob("*")):
                if not path.is_file():
                    continue
                ref = path.relative_to(self._data_root).as_posix()
                if ref in referenced:
                    continue
                try:
                    path.unlink()
                    removed.append(ref)
                except OSError as exc:
                    raise ArtifactStoreError(
                        f"could not sweep orphan artifact {ref!r}: {exc}",
                        code=ErrorCode.ARTIFACT_WRITE_FAILED,
                    ) from exc
            if run_dir.is_dir() and not any(run_dir.iterdir()):
                try:
                    run_dir.rmdir()
                except OSError:  # pragma: no cover - raced with a concurrent write
                    pass
        return removed

    def clear_staging(self) -> int:
        """Remove staged/in-progress files from the temporary workspace
        (SECURITY §2 "temp files have a cleanup policy", §4 "staging files
        cleaned after run", §4 "clear UI/settings for cache, staging").

        Deletes each top-level entry under the staging root but keeps the root
        itself; returns the number of entries removed. Only the temp workspace is
        touched — durable run artifacts and exports are never cleared here."""
        removed = 0
        if not self._staging_root.is_dir():
            return removed
        for entry in sorted(self._staging_root.iterdir()):
            if entry.is_dir():
                self._rmtree(entry)
            else:
                try:
                    entry.unlink()
                except OSError as exc:
                    raise ArtifactStoreError(
                        f"could not clear staging {entry.name!r}: {exc}",
                        code=ErrorCode.ARTIFACT_WRITE_FAILED,
                    ) from exc
            removed += 1
        return removed

    def clear_cache(self) -> int:
        """Clear the rebuildable cache directory (SECURITY §2 "cache is
        rebuildable, may be cleared"). Returns entries removed. Because nothing
        unique lives in the cache, this never destroys a result — the metadata
        store and artifact files are untouched."""
        removed = 0
        if not self._cache_root.is_dir():
            return removed
        for entry in sorted(self._cache_root.iterdir()):
            if entry.is_dir():
                self._rmtree(entry)
            else:
                try:
                    entry.unlink()
                except OSError as exc:
                    raise ArtifactStoreError(
                        f"could not clear cache {entry.name!r}: {exc}",
                        code=ErrorCode.ARTIFACT_WRITE_FAILED,
                    ) from exc
            removed += 1
        return removed


__all__ = [
    "STORE_PREFIX",
    "DEFAULT_FILENAMES",
    "KIND_RETENTION",
    "RetentionPolicy",
    "ArtifactStoreError",
    "ArtifactStore",
    "retention_for",
]
