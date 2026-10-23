"""Data Profiling Engine — desktop host entry point (M0-T1/T2).

Opens the pywebview window and loads the built React frontend from local
files over ``file://`` — no local HTTP/TCP server (ADR-005), HashRouter
handles SPA routing on ``file://`` (ADR-013). The bridge facade
(:class:`BridgeFacade`) is attached as ``js_api`` so the WebView can call
allow-listed commands via ``window.pywebview.api.bridge_send``.

Heavy-work worker process lands in M0-T3.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import threading
import time
from pathlib import Path

import webview

from data_profiling_engine.bridge import BridgeFacade

# frontend/dist is resolved relative to the repo root (dev) or the bundled
# app directory (packaged — PyInstaller spec lands in M0-T4).
REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_INDEX = REPO_ROOT / "frontend" / "dist" / "index.html"

APP_TITLE = "Data Profiling Engine"

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
    if not FRONTEND_INDEX.is_file():
        raise SystemExit(
            f"Frontend build not found at {FRONTEND_INDEX}. "
            "Run `npm run build` inside frontend/ first."
        )
    return FRONTEND_INDEX


def _unwrap_js_result(value: object) -> object:
    """pywebview returns a Future on some platforms and a bare value on others."""
    if isinstance(value, concurrent.futures.Future):
        try:
            return value.result(timeout=2)
        except concurrent.futures.TimeoutError:
            return "<timeout>"
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Data Profiling Engine desktop host")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="open the window, exercise the bridge echo, auto-close (Phase-0 spike verification)",
    )
    args = parser.parse_args(argv)

    index = resolve_index()
    facade = BridgeFacade()
    window = webview.create_window(APP_TITLE, str(index), js_api=facade)

    if args.smoke:
        def _run_smoke() -> None:
            # Give the WebView time to inject ``window.pywebview.api``.
            time.sleep(2)
            try:
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
            time.sleep(0.5)
            window.destroy()

        threading.Thread(target=_run_smoke, daemon=True).start()

    webview.start()
    if args.smoke:
        print("SMOKE_OK: window opened and closed cleanly, index=" + str(index))
    return 0


if __name__ == "__main__":
    sys.exit(main())
