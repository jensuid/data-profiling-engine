"""OS-standard application directories (M3-T1; SECURITY §2, §6.5).

Directory separation is **binding**: the install/application directory is
never the user-data directory, so an upgrade (which replaces the app bundle)
cannot destroy run history, and the metadata database survives in user space.
Nothing here is hard-coded — locations come from the platform's standard
environment variables / conventions (SECURITY §2 table):

| Kind | macOS | Windows | fallback (dev / Linux) |
|---|---|---|---|
| User data (config, SQLite metadata, run history, findings) | ``~/Library/Application Support/<App>`` | ``%APPDATA%\\<App>`` | ``~/.local/share/<App>`` |
| Cache (re-creatable derived data) | ``~/Library/Caches/<App>`` | ``%LOCALAPPDATA%\\<App>\\Cache`` | ``~/.cache/<App>`` |
| Temporary workspace | ``tempfile.gettempdir()`` (macOS) | ``%TEMP%`` | ``tempfile.gettempdir()`` |

Two safety guarantees are enforced here rather than left to callers:

* ``DPE_DATA_DIR`` / ``DPE_CACHE_DIR`` environment overrides let a packaged
  build, a test, or a CI run point the whole layer at a scratch directory
  without changing code (per-user installs on Windows are a ``%LOCALAPPDATA%``
  variant, still outside the app dir).
* :func:`ensure_outside_app_bundle` refuses a data/cache root that lives
  *inside* the frozen application bundle (PyInstaller ``sys._MEIPASS`` /
  ``sys.executable`` dir) — SECURITY §2 "install directory must be separate
  from user data". An artifact written there would be replaced on upgrade.
"""
from __future__ import annotations

import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Optional

#: Product folder name used inside the OS-standard parent directory. Kept
#: free of spaces so it is safe in paths, env vars and installer scripts.
DEFAULT_APP_NAME = "DataProfilingEngine"

#: Env overrides (documented in SECURITY §2 as "resolved via OS-standard
#: locations, never hard-coded" — the override is the test/packaging hook).
DATA_DIR_ENV = "DPE_DATA_DIR"
CACHE_DIR_ENV = "DPE_CACHE_DIR"

#: File name of the SQLite metadata database inside the user-data directory
#: (ADR-008: registry, runs, jobs, config versions, findings metadata).
METADATA_DB_NAME = "metadata.sqlite3"

#: Sub-directory of the user-data directory holding pre-migration database
#: backups (SECURITY §6.5 "keep a pre-migration backup of SQLite").
BACKUP_SUBDIR = "backups"

#: Sub-directory of the user-data directory used by the artifact store
#: (M3-T2 owns the layout/lifecycle; the name is reserved here so the two
#: layers can never drift apart).
ARTIFACT_SUBDIR = "artifacts"


class DirectoryPolicyError(RuntimeError):
    """A resolved directory violates the SECURITY §2 separation policy."""


def _is_windows() -> bool:
    return platform.system() == "Windows"


def app_bundle_dir() -> Optional[Path]:
    """The read-only application/install directory, when it is distinguishable.

    Frozen (PyInstaller) builds: ``sys._MEIPASS`` when set, else the parent of
    the executable. In a source checkout this returns the repo/package root,
    which callers must NOT use as a data dir — hence the explicit guard in
    :func:`ensure_outside_app_bundle` rather than a silent fallback.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass).resolve()
    frozen = getattr(sys, "frozen", False)
    if frozen and sys.executable:
        return Path(sys.executable).resolve().parent
    return None


def ensure_outside_app_bundle(path: "Path | str", *, label: str) -> Path:
    """Return ``path`` resolved, raising :class:`DirectoryPolicyError` when it
    is (or lives inside) the application bundle directory. Upgrade replaces
    the app directory, so nothing user-owned may be written there (SECURITY §2,
    §6.5)."""
    candidate = Path(path).expanduser().resolve()
    bundle = app_bundle_dir()
    if bundle is None:
        return candidate
    if candidate == bundle or bundle in candidate.parents:
        raise DirectoryPolicyError(
            f"{label} path {candidate} is inside the application bundle {bundle}; "
            "user data must live in the OS-standard user directory (SECURITY §2) "
            "so upgrades never destroy results"
        )
    return candidate


def _xdg_or_home(xdg_var: str, home_subpath: str) -> Path:
    home = Path.home()
    xdg = os.environ.get(xdg_var)
    if xdg:
        return Path(xdg).expanduser()
    return home / home_subpath


def default_data_root(*, app_name: str = DEFAULT_APP_NAME) -> Path:
    """Platform-standard *user data* directory (SECURITY §2, never hard-coded).

    ``DPE_DATA_DIR`` overrides it (tests / per-user packaging); the override is
    still checked against the app-bundle policy.
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return ensure_outside_app_bundle(override, label="user data")
    if _is_windows():
        appdata = os.environ.get("APPDATA")
        base = Path(appdata).expanduser() if appdata else Path.home() / "AppData" / "Roaming"
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:  # Linux / other POSIX dev environments (XDG data home)
        base = _xdg_or_home("XDG_DATA_HOME", ".local/share")
    return ensure_outside_app_bundle(base / app_name, label="user data")


def default_cache_root(*, app_name: str = DEFAULT_APP_NAME) -> Path:
    """Platform-standard *cache* directory; may be cleared at any time
    (SECURITY §2 — cache is rebuildable, so nothing unique lives here)."""
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return ensure_outside_app_bundle(override, label="cache")
    if _is_windows():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local).expanduser() if local else Path.home() / "AppData" / "Local"
        candidate = base / app_name / "Cache"
    elif platform.system() == "Darwin":
        candidate = Path.home() / "Library" / "Caches" / app_name
    else:
        candidate = _xdg_or_home("XDG_CACHE_HOME", ".cache") / app_name
    return ensure_outside_app_bundle(candidate, label="cache")


def default_temp_root(*, app_name: str = DEFAULT_APP_NAME) -> Path:
    """Temporary workspace (staging / in-progress artifacts); cleaned by
    policy, so it must never hold a result the user cannot rebuild."""
    base = Path(tempfile.gettempdir())
    return ensure_outside_app_bundle(base / app_name, label="temporary workspace")


def resolve_data_root(
    override: "Path | str | None" = None,
    *,
    app_name: str = DEFAULT_APP_NAME,
) -> Path:
    """Effective user-data directory: explicit ``override`` (bridge/CLI flag or
    a test's ``tmp_path``) wins, else :func:`default_data_root`."""
    if override is not None:
        return ensure_outside_app_bundle(override, label="user data")
    return default_data_root(app_name=app_name)


def ensure_private_dir(path: "Path | str") -> Path:
    """Create ``path`` (parents included) and keep it user-only.

    Owner-only permissions on POSIX (``0700``) because the metadata store and
    the artifacts it references can point at user data files (SECURITY §4 file
    access scope). On Windows the ACL is inherited from the user profile, which
    is already per-user, so no chmod equivalent is attempted."""
    target = Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    if not _is_windows():
        try:
            target.chmod(0o700)
        except OSError:  # read-only filesystem / already correct — non-fatal
            pass
    return target


def metadata_db_path(
    data_root: "Path | str | None" = None,
    *,
    app_name: str = DEFAULT_APP_NAME,
) -> Path:
    """Path of the SQLite metadata database inside the user-data directory."""
    root = resolve_data_root(data_root, app_name=app_name)
    return root / METADATA_DB_NAME


def backups_dir(
    data_root: "Path | str | None" = None,
    *,
    app_name: str = DEFAULT_APP_NAME,
) -> Path:
    """Directory for pre-migration database backups (SECURITY §6.5)."""
    root = resolve_data_root(data_root, app_name=app_name)
    return root / BACKUP_SUBDIR


def artifacts_dir(
    data_root: "Path | str | None" = None,
    *,
    app_name: str = DEFAULT_APP_NAME,
) -> Path:
    """Root of the file artifact store (layout + lifecycle: M3-T2)."""
    root = resolve_data_root(data_root, app_name=app_name)
    return root / ARTIFACT_SUBDIR


__all__ = [
    "DEFAULT_APP_NAME",
    "DATA_DIR_ENV",
    "CACHE_DIR_ENV",
    "METADATA_DB_NAME",
    "BACKUP_SUBDIR",
    "ARTIFACT_SUBDIR",
    "DirectoryPolicyError",
    "app_bundle_dir",
    "ensure_outside_app_bundle",
    "default_data_root",
    "default_cache_root",
    "default_temp_root",
    "resolve_data_root",
    "ensure_private_dir",
    "metadata_db_path",
    "backups_dir",
    "artifacts_dir",
]
