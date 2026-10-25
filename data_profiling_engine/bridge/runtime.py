"""Application-level bridge runtime context (M5-T2, progress push M5-T3).

The real ``profile.*`` handlers need two process-wide resources the pure spike
never did: a **user-data root** (where the artifact store + SQLite metadata
store live) and a **job manager wired to that store**. They are held here as a
small, explicitly-configurable singleton so:

* the packaged app host configures one root at startup
  (:func:`configure_runtime`), and every ``profile.start`` lands artifacts and
  run rows there; and
* a test redirects the whole context to a ``tmp_path`` (and back) without
  touching the real user-data directory or the OS default.

The context also carries the **optional** §5.5 progress push sink
(:func:`configure_status_push`): the bridge never owns a WebView, so the host
installs a sink over its own window (:func:`window_push_sink`) and every manager
we materialise forwards its status transitions there — the same §5.3 object the
UI polls, so neither transport needs a special case.

Nothing here decides *where* the root lives — :mod:`storage.paths` owns that
resolution (SECURITY §2, bundle separation). This module just carries the chosen
value down to the manager and, through it, to the worker process (which rebuilds
its own stores from the ``data_root`` string handed over in the job args).

The manager is created lazily so importing the bridge never opens a SQLite
handle: the first real job (or an explicit accessor) materialises it.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from .envelope import PAYLOAD_CAP_BYTES
from .worker.manager import JobManager

_lock = threading.RLock()
_data_root: Optional[Path] = None
_metadata_store: Optional[object] = None
_profile_manager: Optional[JobManager] = None
_push_sink: Optional[Callable[[dict], None]] = None
_dialog_provider: Optional[Callable[[], Optional[str]]] = None

#: The global the WebView may define to receive a §5.5 push. A fixed name in a
#: fixed JS expression — the only interpolated text is our own serialised status
#: object, never caller input (SECURITY §3).
JS_STATUS_CALLBACK = "__dpeJobStatus"


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
    context. The §5.5 push sink is deliberately *not* reset here — it is the
    host's choice, not part of the data-root context; clear it explicitly with
    :func:`configure_status_push(None)`."""
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
            # Every manager this context hands out observes §5.5 transitions and
            # forwards them to whatever sink the host installed (a no-op while
            # no sink is set, so a polling-only host pays nothing).
            _profile_manager.add_status_listener(_forward_status)
        manager = _profile_manager
    if fresh:
        # Reconcile orphaned open jobs once, when the manager first materialises
        # (not on every poll) — §6.3 recovery on app restart.
        manager.recover_interrupted_jobs()
    return manager


# ---------------------------------------------------------------------------
# §5.5 optional progress push (M5-T3). Polling `profile.get_status` stays the
# authoritative path; this adds the host→JS hint channel over the SAME status
# object, so the UI never needs to know which transport delivered an update.
# ---------------------------------------------------------------------------


def configure_status_push(sink: Optional[Callable[[dict], None]] = None) -> None:
    """Install the process-wide push sink, or clear it with ``None``.

    The sink receives the canonical §5.3 status dict (identical to what
    ``profile.get_status`` returns) on every transition, off the manager's
    dispatcher thread. The app host installs one once it has a window
    (:func:`window_push_sink`); a test installs a fake. Exceptions escaping a
    sink are swallowed by the manager — a dead WebView must never look like a
    job failure (§5.5: push is optional, poll is truth)."""
    global _push_sink
    with _lock:
        _push_sink = sink


def status_push_enabled() -> bool:
    """Whether a push sink is currently installed (host diagnostics/tests)."""
    with _lock:
        return _push_sink is not None


def _forward_status(status: dict) -> None:
    """Manager listener: hand one status to the host sink, if the host set one."""
    sink = _push_sink
    if sink is not None:
        sink(status)


def window_push_sink(window: Any) -> Callable[[dict], None]:
    """Build the §5.5 sink that delivers a status into a pywebview window.

    Emits a fixed expression — ``window.__dpeJobStatus(<json>)`` — whose only
    interpolated value is our own serialised status object (no caller input
    reaches JS, SECURITY §3). A WebView that has not defined the callback simply
    ignores pushes; a status that would exceed the §5.4 message cap is dropped
    rather than truncated, because the UI can always poll the same object."""

    def _push(status: dict) -> None:
        try:
            payload = json.dumps(status)
        except (TypeError, ValueError):
            return
        if len(payload.encode("utf-8")) > PAYLOAD_CAP_BYTES:
            return
        try:
            window.evaluate_js(
                f"window.{JS_STATUS_CALLBACK} && {JS_STATUS_CALLBACK}({payload});"
            )
        except Exception:  # noqa: BLE001 — a stalled/closed window is not a job failure
            return

    return _push


# ---------------------------------------------------------------------------
# ADR-022 native file dialog (M7-T4b). The mirror image of the push sink: the
# WebView *asks* the host for one thing — a user-picked source-file path — and
# the host answers through a provider it installed itself. Like the sink, the
# bridge owns no WebView: the callable is duck-typed, built in main.py over
# ``window.create_file_dialog``, so this module still imports nothing webview.
# ---------------------------------------------------------------------------


def configure_file_dialog(provider: Optional[Callable[[], Optional[str]]] = None) -> None:
    """Install the process-wide file-dialog provider, or clear it with ``None``.

    The provider opens the OS-native pick dialog (host-thread blocking is
    correct for a user-modal dialog) and returns the chosen path, or ``None``
    when the user cancels. The app host installs one once it has a window; a
    headless/test context leaves it unset and ``dialog.open_file`` answers the
    typed ``DIALOG_UNAVAILABLE`` error. Deliberately *not* reset by
    :func:`configure_runtime` — it is the host's choice, not the data-root
    context (same discipline as the §5.5 push sink)."""
    global _dialog_provider
    with _lock:
        _dialog_provider = provider


def get_file_dialog_provider() -> Optional[Callable[[], Optional[str]]]:
    """The currently registered provider (``None`` = no host dialog)."""
    with _lock:
        return _dialog_provider


__all__ = [
    "configure_runtime",
    "configure_status_push",
    "configure_file_dialog",
    "get_file_dialog_provider",
    "data_root",
    "get_metadata_store",
    "get_profile_manager",
    "status_push_enabled",
    "window_push_sink",
    "JS_STATUS_CALLBACK",
]
