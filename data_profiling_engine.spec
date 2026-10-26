# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec — packaged desktop app (ADR-009; SECURITY §6.1, §6.3–§6.6).

Produces the per-OS artefacts that feed the M7-T3 installer matrix (SECURITY §6.6):

* macOS   -> a windowed ``DataProfilerEngine.app`` bundle (console=False + BUNDLE
  below), which ``packaging/macos/build_app_dmg.sh`` wraps into a ``.dmg`` and
  ``packaging/macos/sign_notarize.sh`` codesigns + notarizes.
* Windows -> a single-file ``DataProfilerEngine.exe`` (no BUNDLE on win32), which
  ``packaging/windows/profiler.nsi`` (NSIS) turns into the installer.

The Phase-0 gate smoke (M0-T1..T4) is still exercised by the frozen binary via
``--smoke``; a windowed .app keeps its stdout when the inner executable is
launched directly from a shell (``DataProfilerEngine.app/Contents/MacOS/
DataProfilerEngine --smoke``) rather than through ``open``, so no console is
needed to capture the ``*SMOKE_RESULT`` markers.

Build (from the repo root, after ``npm run build`` in frontend/):

    .venv/bin/python -m PyInstaller --clean --noconfirm data_profiling_engine.spec

Signing/notarization and the ``.dmg``/NSIS wrappers are NOT done by this spec —
they run from ``packaging/`` so the Developer ID / notarization credentials stay
out of the repo (SECURITY §7, AGENTS §7) and are supplied via environment.
"""
from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Bundle identity is env-overridable so a real reverse-DNS id / version can be
# injected at release time without touching the spec (never hardcode org facts).
APP_NAME = os.environ.get("DPE_APP_NAME", "DataProfilerEngine")
BUNDLE_ID = os.environ.get("DPE_BUNDLE_ID", "com.dataprofilingengine.desktop")
APP_VERSION = os.environ.get("DPE_APP_VERSION", "0.1.0")

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

# M7-T5: same class of package data for the JSON Schemas (CONTRACTS §1). The
# frozen app builds :data:`schemas.RAW_SCHEMAS` by globbing the package
# directory, so an uncollected ``schemas/`` left it EMPTY and every
# ``S.validate(...)`` raised ``KeyError`` — surfacing at ``profile.start`` as
# ``BRIDGE_REQUEST_INVALID: handler failed: KeyError`` while dev mode and the
# automated suite stayed green (SECURITY §6.2; guarded by
# ``tests/test_packaging_assets.py``). Named by PATH like ``reporting_assets``:
# ``collect_data_files`` resolves the package through the analysis
# interpreter's import system and, on a clean build, answered "skipping data
# collection … as it is not a package" — i.e. collected NOTHING, reinstating
# exactly the failure this entry exists to prevent. A literal directory mapping
# cannot silently resolve to nothing.
schemas_assets = (
    str(PROJECT_ROOT / "data_profiling_engine" / "schemas"),
    "data_profiling_engine/schemas",
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
    datas=[(str(FRONTEND_DIST), "frontend/dist"), reporting_assets, schemas_assets]
    + duckdb_datas
    + webview_datas,
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

# Per-OS frozen form (SECURITY §6.3 macOS, §6.4 Windows; ADR-009).
#
# macOS  -> windowed (console=False) **onedir** tree wrapped in a .app bundle.
#   PyInstaller deprecates onefile+.app as *clashing with macOS codesign/
#   notarization* — precisely the M7-T3 leg — so the bundle is a real onedir
#   tree (COLLECT + BUNDLE), not a single file. A windowed .app still writes
#   stdout when its inner executable is launched directly from a shell
#   (``DataProfilerEngine.app/Contents/MacOS/DataProfilerEngine --smoke``), so
#   the §5.2 smoke markers stay capturable without a console.
#
# Windows -> single-file onefile **console** exe, unchanged from the M0 Phase-0
#   spike that produced ``smoke_win.txt``. A windowed Windows exe detaches from
#   the console and could not capture ``--smoke > smoke_win.txt``; NSIS wraps
#   this exe directly. Dropping Windows to a windowed build (with file-based
#   smoke diagnostics) is a deferred refinement, not part of this gate.
if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name=APP_NAME,
    )
    bundle = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        # Correct PyInstaller 6.x kwarg names: `bundle_identifier` -> CFBundleIdentifier,
        # `version` -> CFBundleShortVersionString, `info_plist` -> merged into Info.plist.
        # (Using the older `id`/`info` names is silently ignored and leaves the plist at
        # PyInstaller defaults — caught by diffing the built Info.plist.)
        bundle_identifier=BUNDLE_ID,
        version=APP_VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": "Data Profiling Engine",
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleShortVersionString": APP_VERSION,
            "NSHighResolutionCapable": True,
            # WKWebView is system-provided; require a reasonable OS floor.
            "LSMinimumSystemVersion": "11.0",
            "NSRequiresAquaSystemAppearance": False,
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        # console=True keeps stdout smoke diagnostics capturable via cmd
        # redirection on Windows — the pathway M0-T5 proved (SECURITY §6.2).
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        # Signing is left to packaging/macos/sign_notarize.sh (macOS) / signtool
        # (Windows), supplied via environment, never baked into the repo (AGENTS §7).
        codesign_identity=None,
        entitlements_file=None,
    )
