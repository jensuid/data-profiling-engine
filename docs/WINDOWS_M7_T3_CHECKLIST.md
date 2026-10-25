---
title: "M7-T3 — Windows Installer-Matrix Runbook"
aliases: [Windows M7-T3 Runbook, Installer Matrix Windows]
tags: [ops, runbook, m7-t3, packaging, release-gate, windows]
status: active
created: 2026-10-26
doc_role: ops-runbook   # execution checklist, NOT a spec. Specs: docs/SECURITY_PACKAGING.md §6, docs/TESTING_BENCHMARKS.md §5.1.
---

# M7-T3 — Windows installer matrix (native build → install → job → export → upgrade → uninstall)

Purpose: close the **Windows half** of the M7-T3 both-OS release gate — the leg
that cannot run from the macOS dev box (BLUEPRINT Q4: *physical/virtual Windows
required, no container substitute*). This is the operator script: run it top to
bottom on a Windows 10/11 x64 machine, paste each marked output line back, and
commit nothing from here (the macOS session reconciles against `STATE.md` / GATE M7).

It extends the Phase-0 spike runbook `WINDOWS_M0_T5_CHECKLIST.md` (same env +
smoke steps) to the full **TESTING §5.1** packaged acceptance. The repo-side
tooling it drives: `data_profiling_engine.spec` (per-OS form), the NSIS template
`packaging/windows/profiler.nsi`, and the shared checklist `docs/M7_T3_INSTALLER_MATRIX.md`.

> [!IMPORTANT] Gate rule (SECURITY §6.6)
> M7-T3 is accepted only when the **packaged, installed** app passes §5.1 on each
> OS, including **upgrade (data preserved)** and **uninstall (data preserved)**.
> A dev-mode run proves nothing about the installer. Do not stamp GATE MVP
> (M7-T5) until this runbook and the macOS sign/notarize leg both report green.

## 0. Prerequisites (native Windows 10/11 x64)

- [ ] Git, **Node.js LTS** (`node -v`), **uv** (`winget install astral-sh.uv`).
- [ ] **Python 3.12** — `uv python install 3.12` (parity with macOS; do not use another version).
- [ ] **NSIS** for `makensis` — `winget install GnuWin32.Nsis` (or NSIS 3.x); confirm `makensis` on PATH.
- [ ] **WebView2 Evergreen runtime** (or Edge). If absent, `--smoke` prints `WEBVIEW2_SMOKE_FAILED` and the installer's §6.4 check warns — record which, do not hide it (SECURITY §6.4).
- [ ] Note machine spec: `winver`, RAM, free disk ≥ 3 GB.

## 1. Env + deps

```powershell
git clone <repo> ; cd new-profiler
uv venv --python 3.12 .venv ; .venv\Scripts\Activate.ps1   # if blocked: Set-ExecutionPolicy -Scope Process Bypass
uv pip install -r requirements.txt
python -c "import duckdb, webview, clr, pythonnet, psutil; print('DEPS_OK', duckdb.__version__, psutil.__version__)"
```
- [ ] `DEPS_OK` line appears (pywebview pulls `pythonnet`/`pywin32`/`comtypes`; a missing `clr` → `uv pip install pythonnet pywin32 comtypes`). `psutil` is required here for the §6 RSS baseline.

## 2. Unit tests (dev, unfrozen)

```powershell
python -m pytest data_profiling_engine/tests -q
```
- [ ] Record the pass count (macOS baseline: 866). Spawn-semantics differences surface first (ADR-006). The RSS-fallback tests exercise the **Windows** `psutil` path here (they skip on macOS).

## 3. Frontend + frozen onefile exe

```powershell
cd frontend ; npm ci ; npm run build ; cd ..
python -m PyInstaller --clean --noconfirm data_profiling_engine.spec
```
- [ ] `Building single-file executable... completed successfully`; exe at `dist\DataProfilerEngine.exe`. **Windows stays a console onefile** (spec `else` branch) — a windowed exe detaches from the console and could not capture the §4 markers. The windowed **`.app`** form is macOS-only.

## 4. Packaged smoke (the §5.2 gate)

Run from `cmd.exe` so stdout is capturable. The `echo` / `worker_echo_job` probes are the M0 spike commands the packaged facade registers **only** behind an env flag (spike is OFF in production by design — without it the two probes correctly return `BRIDGE_COMMAND_UNKNOWN`):

```cmd
set DPE_ENABLE_SPIKE_COMMANDS=1
dist\DataProfilerEngine.exe --smoke > smoke_win.txt 2>&1
```
A window opens on the Home page from `file://`, then auto-closes (≤ ~15 s). Paste back **all** marker lines from `smoke_win.txt`:
- [ ] `WEBVIEW2_SMOKE_RESULT: runtime_found pv=<version>` (SECURITY §6.4)
- [ ] `DUCKDB_SMOKE_RESULT: version=... rows=[(42,)]` (native `_duckdb.pyd` collected — ADR-009 top risk)
- [ ] `WORKER_SMOKE_RESULT: job=... status=SUCCEEDED progress=...` (frozen **spawn** worker — the Windows-specific proof)
- [ ] `BRIDGE_SMOKE_RESULT: {...'echo'...'ping'...}` (JS↔Python bridge round-trip)
- [ ] `SMOKE_OK: window opened and closed cleanly, index=C:\...\_MEIPASS\frontend\dist\index.html` (`file://` from _MEIPASS)

## 5. NSIS installer + the §5.1 product run

```powershell
makensis /DPRODUCT_VERSION=0.1.0 packaging\windows\profiler.nsi
#    -> DataProfilerEngine-0.1.0-win64.exe   (per-user install to $LOCALAPPDATA\Programs\Data Profiling Engine)
```
Install on a **clean** machine, then run against the *installed* app (not the raw exe):
- [ ] **Install → launch** — window opens from `file://`, no dev server (ADR-010/013).
- [ ] **CSV and Parquet** load via supported connectors; no other connector reachable (ADR-011).
- [ ] **One full job** in the **worker**: UI stays responsive; job observable + cancellable; stats/distributions/missingness/uniqueness present.
- [ ] Quality rules: `PASS`/`FAIL`/`WARN`/`ERROR` distinct (FAIL ≠ ERROR).
- [ ] Findings carry evidence + metric/rule refs + `fingerprint` (ADR-016).
- [ ] Every metric tagged `exact`/`approximate`/`sampled`; sampled marked + seed recorded (ADR-015).
- [ ] **Export**: HTML (self-contained/offline) + JSON + CSV issue export from the same result; PII-like columns masked (SECURITY §5).
- [ ] Run has `run_id`, config version/hash, status, duration, error log, engine version.
- [ ] **Source files not modified** (checksum before/after).
- [ ] `netstat -ano | findstr LISTEN` shows **no listener** for the app PID (ADR-005 — no HTTP/TCP server).
- [ ] **Double-click without console** launches (record any SmartScreen/AV interference; do not disable AV to hide it — SECURITY §6.2).

## 6. Packaged baseline (RSS + guarantees) — with the honest Windows method

The `benchmark` module is **not** bundled in the frozen exe; run the CLI in the same
Windows env (this is the packaged-machine baseline, §5.1):
```powershell
python -m data_profiling_engine.benchmark baseline
python -m data_profiling_engine.benchmark guarantees
```
- [ ] Record `baseline` wall-clock + `guarantees` (cancellation latency, crash recovery), and the **`peak_rss_method`** field in each case's JSON.
- [ ] **RSS on Windows is a `psutil` SAMPLED peak** (`peak_rss_method = "psutil_sampled_peak"`), not the POSIX `ru_maxrss` high-water — label it as such in the report, never as exact (ADR-015). The macOS `resource` series remains the reference curve; the Windows run proves no-crash completion + the sampled RSS trend.

## 7. Upgrade + uninstall data-preservation (SECURITY §6.5)

- [ ] **Upgrade (data preserved):** reinstall with a bumped `/DPRODUCT_VERSION` over the existing app dir; confirm prior runs/exports under `%APPDATA%` remain present + readable; a pre-migration SQLite backup exists; `schema_version` unchanged or additively bumped (CONTRACTS §1).
- [ ] **Uninstall (data preserved):** ARP uninstall removes only `$INSTDIR` + shortcuts + its registry key; **`%APPDATA%` user data must survive** (the NSIS uninstall section deliberately never touches it). Removing user data is an explicit opt-in only.

## 8. Report back (closes the Windows leg)

Send: the §4 marker lines from `smoke_win.txt`, the §5/§7 checklists with pass/fail per line, the `sales_deals` fixture findings diff, the `baseline`/`guarantees` JSONs (with the ADR-021 ceilings + the `psutil_sampled_peak` label), `winver`, exe + installer sizes, build elapsed time, and any workaround. The macOS session verifies against TESTING §5.1 and updates `STATE.md` M7-T3 + GATE M7 only when **both** OS legs report green.

## 9. Known Windows risks (what a failure means)

| Symptom | Likely cause | Action per docs |
|---|---|---|
| `WEBVIEW2_SMOKE_FAILED`, blank window | No WebView2 Runtime | Install Evergreen runtime; graceful-error path is the §6.4 bootstrapper's job — confirm the exe doesn't hard-crash silently |
| `WORKER_SMOKE_FAILED` / exe exits instantly | spawn/freeze bootstrap in frozen child | Gate failure — revise ADR-009, do not code around it |
| `ImportError: _duckdb` / `clr` at startup | native-lib / pythonnet collection gap in spec | Report exact error; spec fix happens in the macOS session |
| RSS `insufficient_data` in `baseline` | `psutil` missing | `uv pip install psutil` (§1); re-run §6 |
| AV quarantines the exe | unsigned onefile heuristic | Record; Authenticode signing via `signtool` is a release-env step, not a repo concern (SECURITY §6.2) |

**Gate rule:** all §4 markers + §5/§7 green on Windows → the Windows leg of M7-T3
passes. Any red → do NOT stamp M7-T5/GATE MVP; log the failure in `STATE.md` and
revise the owning ADR (ADR-003/009/010/013) rather than working around it.
