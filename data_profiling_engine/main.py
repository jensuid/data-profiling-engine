"""Data Profiling Engine — desktop host entry point (M0-T1/T2/T3).

Opens the pywebview window and loads the built React frontend from local
files over ``file://`` — HashRouter handles SPA routing on ``file://`` (ADR-013).
We add no network service of our own (ADR-005); the only listener that appears
is pywebview's internal loopback static-file server, a scoped, documented
exception (ADR-023 Option A) — no profiling data or bridge traffic crosses it
(``js_api`` rides the native ``jsBridge`` handler on macOS / WebView2 message
channel on Windows). The bridge facade
(:class:`BridgeFacade`) is attached as ``js_api`` so the WebView can call
allow-listed commands via ``window.pywebview.api.bridge_send``.

M0-T3: heavy work runs in a spawn-method ``multiprocessing`` worker
(:mod:`data_profiling_engine.bridge.worker`); ``freeze_support()`` keeps the
child process safe under a PyInstaller-frozen build (ADR-006/009).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import webview
import webview.http

from data_profiling_engine.bridge import (
    BridgeFacade,
    configure_file_dialog,
    configure_status_push,
    window_push_sink,
)

# frontend/dist is resolved relative to the repo root (dev) or the bundled
# app directory (packaged). Under a PyInstaller freeze the bundle's read-only
# data tree lives under ``sys._MEIPASS`` (onefile) or the exe dir (onedir);
# ADR-009/SECURITY §6.2 — the spec stages ``frontend/dist`` there.
REPO_ROOT = Path(__file__).resolve().parent.parent

APP_TITLE = "Data Profiling Engine"


def _bundle_root() -> Path:
    """Directory holding the bundled ``frontend/dist`` for this run mode."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return REPO_ROOT

# JS probes used by ``--smoke`` to exercise the ADR-005 pathway end-to-end.
# ``pywebview.api.<method>`` returns a Promise; ``evaluate_js`` serialises a
# Promise as ``{}``, so we stash the resolution on ``window`` and poll.
_BRIDGE_SMOKE_FIRE_JS = (
    "window.__smoke = {done: false, result: null};"
    "window.pywebview.api.bridge_send("
    "{command:'echo', request_id:'smoke-1', payload:{message:'ping'}})"
    ".then(r => { window.__smoke.result = r; window.__smoke.done = true; })"
    ".catch(e => { window.__smoke.result = {error: String(e)}; window.__smoke.done = true; });"
    "'FIRED'"
)
_BRIDGE_SMOKE_POLL_JS = "JSON.stringify(window.__smoke || {done: false, result: null})"


def resolve_index() -> Path:
    index = _bundle_root() / "frontend" / "dist" / "index.html"
    if not index.is_file():
        raise SystemExit(
            f"Frontend build not found at {index}. "
            "Run `npm run build` inside frontend/ first."
        )
    return index


def _run_webview2_smoke() -> None:
    """M0-T5 (Windows): probe the WebView2 Runtime before opening a window.

    SECURITY §6.4 requires an explicit availability check (bootstrapper or
    documented prerequisite); pywebview's edgechromium backend fails without
    it. Reads the Evergreen runtime version out of the registry — the same
    check the WebView2 loader performs. No-op on non-Windows OSes.
    """
    if sys.platform != "win32":
        return
    import winreg

    keys = (
        r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    )
    for hive, path in ((winreg.HKEY_LOCAL_MACHINE, keys[0]),
                       (winreg.HKEY_LOCAL_MACHINE, keys[1]),
                       (winreg.HKEY_CURRENT_USER, keys[1])):
        try:
            with winreg.OpenKey(hive, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
            print(f"WEBVIEW2_SMOKE_RESULT: runtime_found pv={version} key={path}")
            return
        except OSError:
            continue
    print("WEBVIEW2_SMOKE_FAILED: WebView2 Runtime not found in registry "
          "(install the Evergreen runtime or ship the bootstrapper — SECURITY §6.4)")


def _run_duckdb_smoke() -> None:
    """M0-T4: validate DuckDB native-lib collection inside the frozen build.

    ADR-009 / SECURITY §6.2 flags ``.dylib``/``.so`` collection as the top
    packaging risk; running a real query through the bundled extension is the
    Phase-0 smoke that proves PyInstaller shipped it (no engine code yet).
    """
    try:
        import duckdb

        rows = duckdb.sql("SELECT 40 + 2 AS answer").fetchall()
        print(f"DUCKDB_SMOKE_RESULT: version={duckdb.__version__} rows={rows}")
    except Exception as exc:  # noqa: BLE001 — spike diagnostic
        print(f"DUCKDB_SMOKE_FAILED: {type(exc).__name__}: {exc}")


def _run_schemas_smoke() -> None:
    """M7-T5: prove the CONTRACTS §1 JSON Schemas shipped in the frozen bundle.

    ``schemas.RAW_SCHEMAS`` is built by globbing the *package directory*, so a
    ``schemas/`` that PyInstaller did not collect leaves it empty and every
    ``S.validate(...)`` raises ``KeyError`` — which the bridge reports as
    ``BRIDGE_REQUEST_INVALID: handler failed: KeyError`` at ``profile.start``.
    Dev mode never reproduces it, hence a marker here (SECURITY §6.2): presence
    of every required file **and** one real validation that must actually
    reject an empty document (a present-but-unused registry would pass
    ``count`` alone).
    """
    try:
        from data_profiling_engine import schemas as S

        missing = [name for name in S.REQUIRED_SCHEMAS if name not in S.RAW_SCHEMAS]
        if missing:
            print(
                "SCHEMAS_SMOKE_FAILED: missing required schema(s) "
                f"{missing} (package data not collected — see spec ``schemas_datas``)"
            )
            return
        probe_errors = S.validate("dataset_config.schema.json", {})
        if not probe_errors:
            print(
                "SCHEMAS_SMOKE_FAILED: dataset_config.schema.json accepted an "
                "empty document (schema loaded but not applied)"
            )
            return
        print(
            f"SCHEMAS_SMOKE_RESULT: count={len(S.RAW_SCHEMAS)} "
            f"required={len(S.REQUIRED_SCHEMAS)} probe_errors={len(probe_errors)}"
        )
    except Exception as exc:  # noqa: BLE001 — spike diagnostic
        print(f"SCHEMAS_SMOKE_FAILED: {type(exc).__name__}: {exc}")


#: Hosts that are loopback-only (never reachable off-box). A listener bound to
#: anything else (``0.0.0.0``/``*``/a LAN address) is a §8.1 violation.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "[::1]", "::ffff:127.0.0.1"})


def _split_host_port(token: str) -> "tuple[str | None, int | None]":
    """Split an lsof/netstat ``host:port`` token (IPv6 keeps its brackets, so a
    right-partition on ``:`` is safe). Returns ``(None, None)`` when there is no
    numeric port."""
    host, sep, port = token.rpartition(":")
    if not sep or not port.isdigit():
        return None, None
    return host, int(port)


def _parse_listener_lines(lines: list[str], *, win: bool) -> list[tuple[int, str, int]]:
    """Parse OS TCP-table text into ``(pid, host, port)`` LISTEN rows.

    Pure (no OS call) so the classification is unit-testable. macOS/Linux
    ``lsof -nP -iTCP -sTCP:LISTEN`` puts the pid at col 1 and the address as the
    token before the trailing ``(LISTEN)`` state; Windows ``netstat -ano -p TCP``
    puts the proto, local ``host:port``, remote, ``LISTENING``, pid."""
    out: list[tuple[int, str, int]] = []
    for line in lines:
        parts = line.split()
        if win:
            # TCP  127.0.0.1:44212  0.0.0.0:0  LISTENING  2376
            if len(parts) < 5 or parts[0] not in ("TCP", "TCPv6") or "LISTENING" not in parts:
                continue
            pid, addr = parts[-1], parts[1]
        else:
            # DataProfi 2376 jensu 4u IPv4 0x.. 0t0 TCP 127.0.0.1:44212 (LISTEN)
            if len(parts) < 9 or not parts[1].isdigit():
                continue
            pid = parts[1]
            addr = parts[-2] if parts[-1].startswith("(") else parts[-1]
        if not pid.isdigit():
            continue
        host, port = _split_host_port(addr)
        if host is None or port is None:
            continue
        out.append((int(pid), host, port))
    return out


def _pywebview_loopback_endpoint() -> "tuple[str, int] | None":
    """The single non-app listener ADR-023 permits, or ``None``.

    pywebview starts its built-in bottle server whenever the UI is loaded from a
    local ``file://`` URL, serving ONLY the bundle's static files on
    ``127.0.0.1:<port>`` (no data/bridge traffic — js_api uses the native
    message handler). Returns ``(host, port)`` only when that server is bound to
    loopback; a non-loopback bind returns ``None`` so it is flagged as an
    offender (§8.1 must fail, per ADR-023's Option-A conditions)."""
    server = getattr(webview.http, "global_server", None)
    address = getattr(server, "address", None)
    if not address:
        return None
    parsed = urlsplit(address)
    host, port = parsed.hostname, parsed.port
    if host in _LOOPBACK_HOSTS and port:
        return host, port
    return None


def _select_port_offenders(
    listeners: list[tuple[int, str, int]],
    *,
    app_pids: set[str],
    allowed: "tuple[str, int] | None",
) -> list[tuple[int, str, int]]:
    """Return the §8.1-violating listeners owned by *app_pids*.

    ``allowed`` is the exact pywebview loopback ``(host, port)`` to permit; only
    that tuple on a loopback host is skipped — every other listener (a
    different port, a non-loopback bind, or any socket of ours when no
    pywebview server is running) is reported, so the check keeps its teeth."""
    offenders: list[tuple[int, str, int]] = []
    for pid, host, port in listeners:
        if str(pid) not in app_pids:
            continue
        if allowed and host in _LOOPBACK_HOSTS and (host, port) == allowed:
            continue
        offenders.append((pid, host, port))
    return offenders


def _run_ports_smoke() -> None:
    """SECURITY §8 bullet 1: "No port bound while app runs (automated check)."

    Queries the OS-native TCP table (macOS/Linux ``lsof``, Windows ``netstat``
    — read-only diagnostics, no third-party dep, so nothing new enters the
    frozen bundle) for LISTEN-state sockets owned by *this* process or its live
    worker children (ADR-006 spawn workers included). OS-provided webview helper
    processes (WKWebView, the WebView2 runtime) are separate pids, out of scope.

    ADR-023 Option A: the one permitted listener is pywebview's internal
    loopback static-file server — matched to its exact ``(host, port)`` and
    verified loopback; anything else our pids bind is a genuine §8.1 violation
    and still FAILs (stop, escalate)."""
    pids = {str(os.getpid())} | {str(p.pid) for p in multiprocessing.active_children()}
    win = sys.platform == "win32"
    if win:
        cmd = ["netstat", "-ano", "-p", "TCP"]
    else:
        # ``lsof -sTCP:LISTEN`` already returns only LISTEN rows.
        cmd = ["lsof", "-nP", "-a", "-iTCP", "-sTCP:LISTEN"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"PORTS_SMOKE_FAILED: could not run {cmd[0]!r}: {exc}")
        return
    listeners = _parse_listener_lines(done.stdout.splitlines(), win=win)
    allowed = _pywebview_loopback_endpoint()
    offenders = _select_port_offenders(listeners, app_pids=pids, allowed=allowed)
    if offenders:
        print(f"PORTS_SMOKE_FAILED: {len(offenders)} unexpected listening socket(s) "
              f"bound by app pids {sorted(pids, key=int)}")
        for pid, host, port in offenders[:5]:
            print(f"  {host}:{port} pid={pid}")
    elif allowed is not None:
        print(f"PORTS_SMOKE_RESULT: only_pywebview_loopback host={allowed[0]} "
              f"port={allowed[1]} pids={sorted(pids, key=int)} "
              "(ADR-023 Option A: pywebview static-file transport, loopback-only, "
              "no data/bridge crosses it)")
    else:
        print(f"PORTS_SMOKE_RESULT: no_listening_sockets pids={sorted(pids, key=int)}")


def _unwrap_js_result(value: object) -> object:
    """pywebview returns a Future on some platforms and a bare value on others."""
    if isinstance(value, concurrent.futures.Future):
        try:
            return value.result(timeout=2)
        except concurrent.futures.TimeoutError:
            return "<timeout>"
    return value


#: ADR-022 (M7-T4b): the open dialog's file-type filter, hard-coded HOST-side
#: to the MVP connectors (ADR-011). JS sends nothing - the dialog.open_file
#: payload is empty - so no caller input reaches the dialog (SECURITY §3), and
#: the returned path still faces the ordinary allow-list on every later read.
SOURCE_FILE_TYPES = ("CSV files (*.csv)", "Parquet files (*.parquet)")


def _window_file_dialog(window):
    """Build the ``dialog.open_file`` provider over this pywebview window.

    Opens the OS-native single-select open dialog and returns the picked path
    (``None`` on cancel). Lives in the host, not the bridge: the bridge owns no
    WebView (same duck-typed discipline as ``window_push_sink``), so only this
    module touches ``webview`` dialog constants."""

    def _open() -> "str | None":
        selected = window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False, file_types=SOURCE_FILE_TYPES
        )
        if not selected:
            return None
        first = selected[0] if isinstance(selected, (list, tuple)) else selected
        return str(first)

    return _open


def main(argv: list[str] | None = None) -> int:
    # Must run before anything else in the frozen entry point: spawned
    # children re-import this module and freeze_support short-circuits them
    # back into multiprocessing's bootstrap (ADR-006, PyInstaller/ADR-009).
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(description="Data Profiling Engine desktop host")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="open the window, exercise the bridge echo, auto-close (Phase-0 spike verification)",
    )
    args = parser.parse_args(argv)

    index = resolve_index()
    if args.smoke:
        # Native-lib check runs first, before the WebView, so a DuckDB
        # collection failure is visible even if the GUI path stalls.
        _run_webview2_smoke()
        _run_duckdb_smoke()
        _run_schemas_smoke()
    facade = BridgeFacade()
    window = webview.create_window(APP_TITLE, str(index), js_api=facade)
    # Optional §5.5 progress push: every status transition is also handed to the
    # WebView as ``window.__dpeJobStatus(<status>)``. Purely additive — a UI that
    # ignores it keeps working by polling ``profile.get_status``, which stays
    # authoritative, and both transports carry the identical §5.3 object. Still
    # host→JS only: no server, no socket (ADR-005).
    configure_status_push(window_push_sink(window))
    # ADR-022: the WebView's Browse button asks the host for a native open
    # dialog through the single allow-listed bridge (``dialog.open_file``).
    configure_file_dialog(_window_file_dialog(window))

    if args.smoke:
        def _run_smoke() -> None:
            # Give the WebView time to inject ``window.pywebview.api``.
            time.sleep(2)
            try:
                _run_worker_smoke(facade)
                window.evaluate_js(_BRIDGE_SMOKE_FIRE_JS)
                payload: str | None = None
                for _ in range(40):  # up to ~4 s
                    time.sleep(0.1)
                    raw = _unwrap_js_result(window.evaluate_js(_BRIDGE_SMOKE_POLL_JS))
                    if isinstance(raw, str):
                        payload = raw
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if parsed.get("done"):
                            print(f"BRIDGE_SMOKE_RESULT: {parsed.get('result')!r}")
                            break
                else:
                    print(f"BRIDGE_SMOKE_TIMEOUT: last={payload!r}")
            except Exception as exc:  # noqa: BLE001 — spike diagnostic
                print(f"BRIDGE_SMOKE_FAILED: {type(exc).__name__}: {exc}")
            # SECURITY §8 bullet 1 — while the app (window + bridge + worker)
            # is genuinely running, before teardown.
            _run_ports_smoke()
            time.sleep(0.5)
            window.destroy()

        threading.Thread(target=_run_smoke, daemon=True).start()

    webview.start()
    if args.smoke:
        print("SMOKE_OK: window opened and closed cleanly, index=" + str(index))
    return 0


def _run_worker_smoke(facade: "BridgeFacade") -> None:
    """M0-T3: bridge facade → spawn worker → status slot, inside the live app.

    Drives the same in-process pathway the WebView uses (facade commands,
    not raw manager calls) so the packaged smoke proves bridge+worker
    coexistence; the JS round-trip for ``echo`` is covered separately by
    ``_BRIDGE_SMOKE_FIRE_JS``.
    """
    try:
        accepted = facade.bridge_send(
            {
                "command": "worker_echo_job",
                "request_id": "smoke-worker-1",
                "payload": {"message": "ping-worker", "total_steps": 4},
            }
        )
        job_id = accepted.get("job_id")
        assert accepted.get("status") == "accepted" and isinstance(job_id, str), accepted
        final: dict = {}
        for _ in range(100):  # up to ~10 s
            time.sleep(0.1)
            polled = facade.bridge_send(
                {
                    "command": "job.get_status",
                    "request_id": "smoke-worker-poll",
                    "payload": {"job_id": job_id},
                }
            )
            final = polled.get("result", {})
            if final.get("status") in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
        print(
            f"WORKER_SMOKE_RESULT: job={job_id} "
            f"status={final.get('status')} progress={final.get('progress')} "
            f"result={final.get('result')}"
        )
    except Exception as exc:  # noqa: BLE001 — spike diagnostic
        print(f"WORKER_SMOKE_FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
