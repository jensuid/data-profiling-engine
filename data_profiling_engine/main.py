"""Data Profiling Engine — desktop host entry point (M0-T1).

Opens the pywebview window and loads the built React frontend from local
files over ``file://`` — no local HTTP/TCP server (ADR-005), HashRouter
handles SPA routing on ``file://`` (ADR-013).

The bridge facade is added in M0-T2; heavy-work worker in M0-T3.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import webview

# frontend/dist is resolved relative to the repo root (dev) or the bundled
# app directory (packaged — PyInstaller spec lands in M0-T4).
REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_INDEX = REPO_ROOT / "frontend" / "dist" / "index.html"

APP_TITLE = "Data Profiling Engine"


def resolve_index() -> Path:
    if not FRONTEND_INDEX.is_file():
        raise SystemExit(
            f"Frontend build not found at {FRONTEND_INDEX}. "
            "Run `npm run build` inside frontend/ first."
        )
    return FRONTEND_INDEX


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Data Profiling Engine desktop host")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="open the window and auto-close after 3s (Phase-0 spike verification)",
    )
    args = parser.parse_args(argv)

    index = resolve_index()
    window = webview.create_window(APP_TITLE, str(index))

    if args.smoke:
        def _close_after_delay() -> None:
            time.sleep(3)
            window.destroy()

        threading.Thread(target=_close_after_delay, daemon=True).start()

    webview.start()
    if args.smoke:
        print("SMOKE_OK: window opened and closed cleanly, index=" + str(index))
    return 0


if __name__ == "__main__":
    sys.exit(main())
