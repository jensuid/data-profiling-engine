---
title: "M0-T5 — Windows Execution Checklist"
aliases: [Windows Spike Runbook]
tags: [ops, runbook, m0-t5]
status: active
created: 2026-10-24
doc_role: ops-runbook   # execution checklist, NOT a spec (see docs/BLUEPRINT.md §2 map)
---

# M0-T5 — Windows native-env build & smoke (runbook for the Windows operator)

Purpose: close the Phase-0 gate on Windows — same smoke as macOS (M0-T1..T4)
in a **PyInstaller onefile build on a native Windows machine/VM** (BLUEPRINT
Phase 0, Q4: physical/virtual Windows required, no container substitutes),
plus **WebView2-runtime detection** (SECURITY §6.4, ADR-009 Win risks).
Acceptance list = `docs/TESTING_BENCHMARKS.md` §5.2.

Everything repo-side that can be prepared from macOS is already done (spec is
platform-aware; `main.py --smoke` prints `WEBVIEW2_SMOKE_*` on Windows). This
file is only the execution script — run it top to bottom, paste each marked
output line back, commit nothing.

## 0. Prerequisites (native Windows 10/11 x64)

- [ ] Git, **Node.js LTS** (`node -v`), **uv** (`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` or `winget install astral-sh.uv`).
- [ ] Python 3.12 — let uv fetch it: `uv python install 3.12` (do **not** use other Python versions; parity with macOS GOTCHA).
- [ ] PowerShell 7 (`winget install Microsoft.PowerShell`) recommended; steps below are PowerShell-syntax.
- [ ] Note machine spec (OS build, RAM, free disk ≥ 3 GB).

## 1. Env + deps

```powershell
git clone <repo> ; cd new-profiler
uv venv --python 3.12 .venv
.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope Process Bypass
uv pip install -r requirements.txt
python -c "import duckdb, webview, clr, pythonnet; print('DEPS_OK', duckdb.__version__)"
```

- [ ] `DEPS_OK` line appears. On Windows pywebview pulls `pythonnet`/`pywin32`/`comtypes`; a missing `clr` import means the extras didn't resolve → `uv pip install pythonnet pywin32 comtypes` and re-check.

## 2. Unit tests (dev, unfrozen)

```powershell
python -m pytest data_profiling_engine/tests -q
```

- [ ] Record the pass count (macOS baseline: 35/35). Spawn-semantics differences surface here first (ADR-006).

## 3. Frontend + frozen build

```powershell
cd frontend ; npm ci ; npm run build ; cd ..
python -m PyInstaller --clean --noconfirm data_profiling_engine.spec
```

- [ ] Build ends with `Building single-file executable... completed successfully`; exe at `dist\DataProfilerEngine.exe` (spec is onefile; the platform-aware hidden-imports block selects `webview.platforms.edgechromium` + `clr` on Windows).
- [ ] If build fails on `FRONTEND_DIST` check → step 3 frontend build didn't produce `frontend\dist\index.html`.

## 4. Packaged smoke (the actual gate)

Run from `cmd.exe` so stdout is capturable (no `timeout` cmd exists on either OS — let the app auto-close; it self-destroys after ≤ ~15 s):

```cmd
dist\DataProfilerEngine.exe --smoke > smoke_win.txt 2>&1
```

A window must open showing the Home page from `file://`, then auto-close.
Paste back **all marker lines** from `smoke_win.txt` and check each:

- [ ] `WEBVIEW2_SMOKE_RESULT: runtime_found pv=<version>` (SECURITY §6.4 detection; `WEBVIEW2_SMOKE_FAILED` = runtime absent — record which, see §6)
- [ ] `DUCKDB_SMOKE_RESULT: version=... rows=[(42,)]` (native `_duckdb.pyd` collected — ADR-009 top risk)
- [ ] `WORKER_SMOKE_RESULT: job=... status=SUCCEEDED progress=100 ...` (frozen **spawn** worker round-trip — the Windows-specific proof)
- [ ] `BRIDGE_SMOKE_RESULT: ...echo...ping...` (JS↔Python bridge round-trip in packaged build)
- [ ] `SMOKE_OK: window opened and closed cleanly, index=C:\...\_MEIPASS\frontend\dist\index.html` (`file://` from _MEIPASS)
- [ ] One launcher double-click without console: if SmartScreen/AV interferes, record it (SECURITY §6.2 "antivirus false positives" risk row) — do not disable AV to hide it.

Also run dev-mode once for parity: `python -m data_profiling_engine.main --smoke`.

## 5. Report back

Send: `smoke_win.txt` (marker lines only), pytest count, OS build (`winver`), exe size, elapsed build time, and any step that needed a workaround. The macOS session verifies against §5.2 and updates `STATE.md` M0-T5 + GATE M0.

## 6. Known Windows risks (what a failure means)

| Symptom | Likely cause | Action per docs |
|---|---|---|
| `WEBVIEW2_SMOKE_FAILED`, window blank/crash | No WebView2 Runtime | Install Evergreen runtime (or Edge), re-run §4; absent-runtime graceful-error path is the M7 bootstrapper's job — just confirm the exe doesn't hard-crash silently |
| `WORKER_SMOKE_FAILED: ...` or exe exits instantly | spawn/freeze bootstrap issue in frozen child | Re-run with `--debug bootloader` build; capture child stderr — this is a gate failure, revise ADR-009, don't code around it |
| `ImportError: _duckdb` / `clr` at startup | collection gap in spec | Add exact error to report; spec fix happens in the macOS session |
| AV quarantines the exe | unsigned onefile heuristic | Record; signing is M7-T3, but the clean-machine result matters for §5.2 |

**Gate rule (BLUEPRINT Phase 0):** all five §4 markers green on Windows → GATE M0 passes (macOS already proven, M0-T1..T4 ✅). Any red → do not start M1; log the failure in STATE.md and revise ADR-003/009/010/013.
