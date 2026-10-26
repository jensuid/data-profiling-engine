"""M7-T5c — guard: bridge readiness is event-driven, never a render snapshot.

pywebview injects ``window.pywebview`` *after* the host page has loaded and only
then dispatches ``pywebviewready``. The packaged React bundle wins that race, so
a single render-time ``bridgeAvailable()`` read is always ``false`` inside the
``.app`` and latches the "Desktop bridge not detected" banner on forever (dev
mode hides it because Vite loads slower). That defect shipped on all four bridge
screens and was only caught by driving the installed build, so pin it here.

This is a static source scan in the same spirit as the no-bare-``duckdb.connect``
and no-server guards: it needs no JS toolchain and fails the moment a new screen
re-introduces the snapshot.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
BRIDGE_DIR = FRONTEND_SRC / "bridge"
HOOK = BRIDGE_DIR / "useBridgeReady.ts"
API = BRIDGE_DIR / "api.ts"

# Every screen that gates its commands on the desktop bridge.
BRIDGE_SCREENS = ("DatasetSelection", "RunConfig", "RunProgress", "Report")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontend_sources() -> list[Path]:
    return [*sorted(FRONTEND_SRC.rglob("*.ts")), *sorted(FRONTEND_SRC.rglob("*.tsx"))]


def test_the_hook_is_the_single_readiness_source() -> None:
    assert HOOK.is_file(), "frontend/src/bridge/useBridgeReady.ts is missing"

    source = _read(HOOK)
    # The injected event is the primary signal; the poll is only the fallback,
    # and it must stay bounded so a plain browser still degrades.
    assert 'addEventListener("pywebviewready"' in source
    assert "removeEventListener(\"pywebviewready\"" in source
    assert "POLL_TIMEOUT_MS" in source
    assert "bridgeAvailable()" in source


def test_no_component_reads_the_predicate_at_render() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _frontend_sources()
        if path not in {HOOK, API} and "bridgeAvailable(" in _read(path)
    ]
    assert offenders == [], (
        "bridge readiness must come from useBridgeReady(); a render-time "
        f"bridgeAvailable() snapshot never updates in the packaged shell: {offenders}"
    )


def test_every_bridge_screen_uses_the_hook() -> None:
    for name in BRIDGE_SCREENS:
        source = _read(FRONTEND_SRC / "pages" / f"{name}.tsx")
        assert "useBridgeReady()" in source, f"{name}.tsx must call useBridgeReady()"
        assert "bridgeAvailable" not in source, (
            f"{name}.tsx must not import bridgeAvailable — use useBridgeReady()"
        )
