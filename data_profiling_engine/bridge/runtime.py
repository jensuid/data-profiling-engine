"""Application-level bridge runtime context (M5-T2).

The real ``profile.*`` handlers need two process-wide resources the pure spike
never did: a **user-data root** (where the artifact store + SQLite metadata
store live) and a **job manager wired to that store**. They are held here as a
small, explicitly-configurable singleton so:

* the packaged app host configures one root at startup
  (:func:`configure_runtime`), and every ``profile.start`` lands artifacts and
  run rows there; and
* a test redirects the whole context to a ``tmp_path`` (and back) without
  touching the real user-data directory or the OS default.

Nothing here decides *where* the root lives — :mod:`storage.paths` owns that
resolution (SECURITY §2, bundle separation). This module just carries the chosen
value down to the manager and, through it, to the worker process (which rebuilds
its own stores from the ``data_root`` string handed over in the job args).

The manager is created lazily so importing the bridge never opens a SQLite
handle: the first real job (or an explicit accessor) materialises it.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .worker.manager import JobManager

_lock = threading.RLock()
_data_root: Optional[Path] = None
_metadata_store: Optional[object] = None
_profile_manager: Optional[JobManager] = None


def configure_runtime(
    data_root: "Path | str | None" = None,
    *,
    metadata_store: Optional[object] = None,
) -> None:
    """(Re)bind the process-wide bridge context.

    ``data_root`` overrides the OS-standard user-data resolution for this
    process (tests, per-user packaging); ``None`` restores the default. Passing
    an explicit ``metadata_store`` (e.g. an ``:memory:`` store) attaches it to
    the manager instead of one lazily built from ``data_root``. Calling this
    discards any previously built manager so the next job picks up the new
    context."""
    global _data_root, _metadata_store, _profile_manager
    with _lock:
        _data_root = Path(data_root).expanduser().resolve() if data_root is not None else None
        _metadata_store = metadata_store
        _profile_manager = None


def data_root() -> Optional[Path]:
    """The configured user-data root, or ``None`` for the OS default."""
    with _lock:
        return _data_root


def get_metadata_store():
    """The host-side :class:`~data_profiling_engine.storage.metadata_store.MetadataStore`.

    Imported lazily so importing the bridge does not require storage/engine
    modules to be loaded (keeps the pure-envelope tests dependency-light)."""
    global _metadata_store
    with _lock:
        if _metadata_store is None:
            from data_profiling_engine.storage.metadata_store import MetadataStore

            _metadata_store = MetadataStore(data_root=_data_root)
        return _metadata_store


def get_profile_manager() -> JobManager:
    """The store-backed :class:`JobManager` the ``profile.*`` handlers drive.

    Created once per configuration; its ``metadata_store`` makes job state
    persistent and recoverable (§6.3). Leftover open jobs from a prior process
    are reconciled the first time the manager is materialised."""
    global _profile_manager
    with _lock:
        fresh = _profile_manager is None
        if fresh:
            _profile_manager = JobManager(metadata_store=get_metadata_store())
        manager = _profile_manager
    if fresh:
        # Reconcile orphaned open jobs once, when the manager first materialises
        # (not on every poll) — §6.3 recovery on app restart.
        manager.recover_interrupted_jobs()
    return manager


__all__ = [
    "configure_runtime",
    "data_root",
    "get_metadata_store",
    "get_profile_manager",
]
