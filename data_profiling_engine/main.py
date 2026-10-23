"""Data Profiling Engine — desktop host entry point (M0-T1/T2/T3).

Opens the pywebview window and loads the built React frontend from local
files over ``file://`` — no local HTTP/TCP server (ADR-005), HashRouter
handles SPA routing on ``file://`` (ADR-013). The bridge facade
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
    facade = BridgeFacade()
    window = webview.create_window(APP_TITLE, str(index), js_api=facade)

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
