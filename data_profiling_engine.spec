# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec — M0-T4 desktop spike (ADR-009; SECURITY §6.2, §6.6).

Produces a per-OS bundle of the pywebview host so the **frozen** app can pass
the Phase-0 gate: M0-T1 window + ``file://`` frontend, M0-T2 bridge echo,
M0-T3 spawn worker job — plus the DuckDB native-extension collection check
that ADR-009 flags as the top packaging risk (SECURITY §6.2).

Build (from the repo root, after ``npm run build`` in frontend/):

    .venv/bin/python -m PyInstaller --clean --noconfirm data_profiling_engine.spec

Run the packaged smoke:

    dist/DataProfilerEngine --smoke

Keep onefile + console for Phase 0: a single self-contained exe that extracts
its data tree to ``sys._MEIPASS`` at runtime, with readable stdout diagnostics
and the ``multiprocessing`` spawn hook applied by PyInstaller. Signing/
notarization + ``.dmg`` and the Windows track are later milestones (M0-T5,
M7-T3); this spec only proves the collect/freeze pathway is viable, as ADR-009
requires before we commit to PyInstaller.
"""
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# ``SPECPATH`` is injected by PyInstaller = directory holding this spec.
PROJECT_ROOT = Path(SPECPATH).resolve()
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

# Fail fast, before a long build, if the frontend assets aren't built — the
# pipeline builds React first (SECURITY §6.6).
if not (FRONTEND_DIST / "index.html").is_file():
    raise SystemExit(
        f"Frontend build missing at {FRONTEND_DIST}. "
        "Run `npm run build` inside frontend/ before packaging."
    )

# DuckDB's Python binding is a compiled extension (``_duckdb.abi3.so`` /
# ``.pyd``) statically linked against libduckdb. Collect both the extension
# binaries and the package's own data files so the frozen import resolves.
duckdb_binaries = collect_dynamic_libs("duckdb")
duckdb_datas = collect_data_files("duckdb")

# pywebview ships JS/CSS runtime assets (native.js, js bundles) it loads at
# runtime by path; without them the WebView cannot inject ``window.pywebview``.
webview_datas = collect_data_files("webview")
webview_binaries = collect_dynamic_libs("webview")

# M4-T2: the vendored Plotly.js the HTML report inlines is package data of
# ``reporting`` — PyInstaller only auto-collects .py files, so name it (the
# report would fail to render in the frozen app without it; SECURITY §6.2).
reporting_assets = (
    str(PROJECT_ROOT / "data_profiling_engine" / "reporting" / "assets"),
    "data_profiling_engine/reporting/assets",
)

# WebView backend is selected per OS at runtime by dynamic import, so the
# platform's backend module must be named explicitly (ADR-003: WKWebView on
# macOS, WebView2/WinForms via pythonnet ``clr`` on Windows; the WinForms and
# WebView2 interop DLLs ship inside the wheel's ``webview/lib`` and are
# already collected by ``collect_data_files("webview")`` above).
if sys.platform == "darwin":
    platform_hiddenimports = ["webview.platforms.cocoa"]
elif sys.platform == "win32":
    platform_hiddenimports = [
        "webview.platforms.edgechromium",
        "webview.platforms.win32",
        "webview.platforms.winforms",
        # pythonnet is imported as `clr` (dotted name confuses static analysis).
        "clr",
    ]
else:
    platform_hiddenimports = []

a = Analysis(
    [str(PROJECT_ROOT / "data_profiling_engine" / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=duckdb_binaries + webview_binaries,
    datas=[(str(FRONTEND_DIST), "frontend/dist"), reporting_assets] + duckdb_datas + webview_datas,
    hiddenimports=[
        # Entry imports these via chains PyInstaller can follow, but the
        # spawn-method worker re-imports the target module in a fresh
        # interpreter, so name it explicitly (SECURITY §6.2 multiprocessing).
        "data_profiling_engine.bridge.worker.process",
        # DuckDB C-extension + lazy function-level import in the smoke check.
        "duckdb",
        "_duckdb",
        *platform_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["polars", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DataProfilerEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=True keeps stdout smoke diagnostics visible for the Phase-0
    # gate (window/file://, bridge echo, worker job, DuckDB query). Drop to
    # a windowed .app only once signing/installer work lands (M7-T3).
    # No COLLECT() below -> single-file onefile bundle (extracts to _MEIPASS).
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
