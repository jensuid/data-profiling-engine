---
title: "Windows GATE-MVP Execution Packet"
aliases: [Windows GATE MVP, Windows Leg Packet, M7-T3 Windows Do-It]
tags: [ops, runbook, m7-t3, m7-t5, release-gate, windows, gate-mvp]
status: active
created: 2026-10-26
doc_role: ops-runbook   # execution packet, NOT a spec. Specs it applies: docs/SECURITY_PACKAGING.md §6/§8, docs/TESTING_BENCHMARKS.md §5.1/§4.3, docs/M7_T3_INSTALLER_MATRIX.md, docs/WINDOWS_M7_T3_CHECKLIST.md.
---

# Windows GATE-MVP Execution Packet — the "do this now" checklist

This is the single operator-facing packet to close the **Windows leg** that GATE
MVP is waiting on. It **consolidates and sequences** two existing runbooks — it
does not replace or restate them. Open the owning doc for any *why*; open this
for *what to run and what to paste back*.

Precedence (AGENTS §1): this packet is subordinate to
[`WINDOWS_M7_T3_CHECKLIST.md`](./WINDOWS_M7_T3_CHECKLIST.md) (the step-by-step
operator script) and [`M7_T3_INSTALLER_MATRIX.md`](./M7_T3_INSTALLER_MATRIX.md)
(the both-OS shared checklist), which in turn are subordinate to the specs. If a
command here disagrees with those, **they win and this file is corrected.**

> [!IMPORTANT] What "done" means (SECURITY §6.6 — do not stamp earlier)
> GATE MVP (M7-T5) is accepted only when the **packaged, installed** app passes
> TESTING §5.1 on **each** OS, including install → job → export → **upgrade
> (data preserved)** → **uninstall (data preserved)**. A dev-mode run proves
> nothing about the installer. The macOS legs (boxes 1–15, 16-macos, 11) are
> already GREEN (see the per-box board in `STATE.md`); **this packet closes the
> two remaining PARTIAL boxes on the Windows side**:
> - **Box 16** — installer matrix, Windows track (§5–§6 below).
> - **Box 17** — packaged benchmark baseline, Windows (§7 below).
> The third still-open gate item, **macOS codesign + notarization**, is a
> separate leg needing a Developer ID (see `M7_T3_INSTALLER_MATRIX.md` §3); it is
> NOT part of this packet.

## 0. Hard constraints carried from the spec pack (never violate)

- **Native Windows 10/11 x64 only** — no container / no CI-only substitute
  (BLUEPRINT Q4). The `--smoke` and §5.1 walk must run on a real desktop session.
- **No HTTP/TCP server** (ADR-005). The only allowed listener is pywebview's
  internal loopback static-file server, per **ADR-023 Option A**; anything else
  is a `PORTS_SMOKE_FAILED` gate-stop.
- **MVP connectors: CSV + Parquet only** (ADR-011). No Excel/SQL/API reachable.
- **Heavy work in the worker process**, UI stays responsive (ADR-006).
- **Windows stays a console onefile `.exe`** — the spec's win32 branch is
  deliberately console (not windowed) so the §4 markers are capturable on
  stdout; the windowed `.app` form is macOS-only (`data_profiling_engine.spec`).
- **Never present a sampled/approximate value as exact** (ADR-015): Windows RSS
  is a `psutil` **sampled** peak — see §7.
- Small local helper choices are yours; **any fundamental problem → stop and
  escalate (AGENTS §8), do not code around it.**

---

## 1. PRE-HANDOFF — run on the macOS box first (blocking prerequisite)

The Windows operator builds from `git clone`; the clone only carries what is
committed **and pushed**. As of the current working tree these must land first:

- [ ] **Commit this session's two UI follow-ups** (`M7-T5: UI follow-ups …`):
      `frontend/src/pages/Report.tsx` (unique-candidate source hint) and
      `frontend/src/pages/DatasetSelection.tsx` (Browse cancel-hang watchdog) +
      the `STATE.md` updates. They are non-gate, but the packaged app should ship
      them, and they must not be lost.
- [ ] **Confirm the already-committed gate fixes are in the branch tip**: box-7
      `is_unique_candidate` full-relation derive (`d3e6719`) and packaged bridge
      readiness `useBridgeReady()` (`fe57c57`). These ARE gate-relevant — without
      `fe57c57` the packaged shell shows "Desktop bridge not detected".
- [ ] **Push to origin — after the L3 deep security review gate** (repo policy:
      every push is pre-checked with the security-scan L3 review; do not skip).
      Local `master` currently sits **ahead of `origin/master`**; without the push
      the clone will not see the fixes.
- [ ] Record the exact commit SHA the Windows box will check out (paste into the
      report-back §8 header). The build must be reproducible to that SHA.

> Do **not** hand a "rebuild `dist/` from current code" job to the operator
> against a stale remote. First action per `M7_T3_INSTALLER_MATRIX.md` §0 is
> always: the remote tip reflects current committed code.

---

## 2. Windows machine prerequisites

Run `winver`, note RAM + free disk (**≥ 3 GB** for the build; more if the §7
baseline runs the larger tiers). From `WINDOWS_M7_T3_CHECKLIST.md` §0:

- [ ] Git, **Node.js LTS** (`node -v`), **uv** (`winget install astral-sh.uv`).
- [ ] **Python 3.12** exactly — `uv python install 3.12` (parity with macOS; not
      another minor).
- [ ] **NSIS 3.x** for `makensis` — `winget install GnuWin32.Nsis` (or equivalent);
      confirm `makensis` is on PATH. **GOTCHA (Windows-box run 2026-10-26):** NSIS
      installs to `C:\Program Files (x86)\NSIS\makensis.exe` but often is **not on
      PATH** — invoke it by full path if `makensis` is not found.
- [ ] **WebView2 Evergreen runtime** (or Edge). If absent, `--smoke` prints
      `WEBVIEW2_SMOKE_FAILED` and the installer warns (§6.4) — record which, do
      not hide it.
- [ ] AV/SmartScreen will be **observed, not disabled** to make the run pass
      (SECURITY §6.2): record any quarantine of the unsigned onefile.

---

## 3. Env + deps + dev unit tests

From `WINDOWS_M7_T3_CHECKLIST.md` §1–§2. In PowerShell:

```powershell
git clone <repo> ; cd new-profiler
git checkout <SHA-from-§1>            # build must match the pushed tip
uv venv --python 3.12 .venv ; .venv\Scripts\Activate.ps1
#   if script execution is blocked: Set-ExecutionPolicy -Scope Process Bypass
uv pip install -r requirements.txt
python -c "import duckdb, webview, clr, pythonnet, comtypes, psutil; print('DEPS_OK', duckdb.__version__, psutil.__version__)"
python -m pytest data_profiling_engine/tests -q
```

- [ ] `DEPS_OK …` line appears (missing `clr` → `uv pip install pythonnet pywin32 comtypes`).
- [ ] **GOTCHA (Windows-box run 2026-10-26):** `comtypes` is **not** pulled
      transitively by pywebview — install `pythonnet pywin32 comtypes` explicitly
      (the `python -c` probe above fails on `clr`/`comtypes` until you do).
- [ ] **Record the pytest pass count** vs the macOS baseline (macOS was 947
      after the UI follow-ups; earlier runs reported 866/925 — the current committed
      tip is what matters). Spawn-semantics differences surface first (ADR-006);
      the RSS-fallback tests exercise the **Windows `psutil`** path here (they
      skip on macOS).

---

## 4. Frontend + frozen onefile exe

```powershell
cd frontend ; npm ci ; npm run build ; cd ..
python -m PyInstaller --clean --noconfirm data_profiling_engine.spec
```

- [ ] `Building single-file executable... completed successfully`; exe at
      `dist\DataProfilerEngine.exe`. Do **not** switch it to windowed (§0) — the
      console form is what emits the §5 markers.

---

## 5. Packaged smoke — the §5.2 marker gate

Run from **`cmd.exe`** so stdout is capturable. The `echo`/`worker_echo_job`
probes are the M0 spike commands, registered **only** behind the env flag (spike
is OFF in production by design — without it they correctly return
`BRIDGE_COMMAND_UNKNOWN`):

> [!WARNING] `set` MUST be on its own line (Windows-box gotcha, 2026-10-26)
> Do **not** collapse this to `set DPE_ENABLE_SPIKE_COMMANDS=1 && …` — cmd hands
> the value **`"1 "` with a trailing space**, and `facade._spike_enabled_from_env`
> matches only `{"1","true","yes"}` exactly, so spike stays OFF and the probes
> correctly return `BRIDGE_COMMAND_UNKNOWN` (looks like a `WORKER_SMOKE_FAILED`
> gate-stop, but is an operator command-form error). Use the two-line form below.

```cmd
set DPE_ENABLE_SPIKE_COMMANDS=1
dist\DataProfilerEngine.exe --smoke > smoke_win.txt 2>&1
```

A window opens on Home from `file://`, then auto-closes (≤ ~15 s). Paste **all**
marker lines from `smoke_win.txt` into the report (§8). The reference shape from
the prior `smoke_win.txt` (older run) was: WEBVIEW2 + DUCKDB + WORKER + BRIDGE +
SMOKE_OK; the current build must **additionally** show SCHEMAS + PORTS:

| Marker | Must read (≈) | Meaning / owner |
|---|---|---|
| `WEBVIEW2_SMOKE_RESULT` | `runtime_found pv=<version>` | WebView2 present (SECURITY §6.4) |
| `DUCKDB_SMOKE_RESULT` | `version=... rows=[(42,)]` | native `_duckdb.pyd` collected (ADR-009 top risk) |
| `SCHEMAS_SMOKE_RESULT` | `count=13 required=13 probe_errors=<n>` | CONTRACTS §1 schemas collected **and** applied; `SCHEMAS_SMOKE_FAILED` = not bundled → `profile.start` dies `handler failed: KeyError` (SECURITY §6.2; M7-T5 run 2) |
| `WORKER_SMOKE_RESULT` | `job=... status=SUCCEEDED ...` | frozen **spawn** worker — the Windows-specific proof (ADR-006) |
| `BRIDGE_SMOKE_RESULT` | `{...'echo'...'ping'...}` | JS↔Python round-trip (ADR-005) |
| `PORTS_SMOKE_RESULT` | `only_pywebview_loopback host=127.0.0.1 port=<n> pids=[...]` (or `no_listening_sockets`) | SECURITY §8.1 / ADR-023 Option A; `PORTS_SMOKE_FAILED` = stop, record, escalate |
| `SMOKE_OK` | `... index=C:\...\_MEI…\frontend\dist\index.html` | `file://` from _MEIPASS (ADR-010) |

- [ ] All markers present. If `WORKER_SMOKE_FAILED` / the exe exits instantly →
      **gate failure, revise ADR-009, do not code around it.**

---

## 6. NSIS installer + the §5.1 product run (this closes box 16 Windows)

```powershell
makensis /DPRODUCT_VERSION=0.1.0 packaging\windows\profiler.nsi
#    -> DataProfilerEngine-0.1.0-win64.exe   (per-user install to $LOCALAPPDATA\Programs\Data Profiling Engine)
```

Install on a **clean** machine, then drive the **installed** app (not the raw
exe). Each line maps to a TESTING §5.1 box already GREEN on macOS — reproduce it
on Windows and mark pass/fail + evidence:

- [ ] **Install → launch** from `file://`, no dev server (ADR-010/013). [box 14]
- [ ] **CSV and Parquet** both load; no other connector reachable (ADR-011). [box 1]
- [ ] Summary + schema profile; physical vs logical types distinguished. [box 2]
- [ ] Statistics + distributions + missingness + uniqueness present. [box 3]
- [ ] Quality rules: `PASS`/`FAIL`/`WARN`/`ERROR` distinct (FAIL ≠ ERROR). [box 4]
- [ ] Findings carry evidence + metric/rule refs + `fingerprint` (ADR-016). [box 5]
- [ ] Recommendations assert no unproven causes. [box 6]
- [ ] Metrics tagged `exact`/`approximate`/`sampled`; sampled carries a seed
      (ADR-015); none claimed exact; `is_unique_candidate` is the full-relation
      fact (must match an independent full-relation `count(DISTINCT)/count(col)`). [box 7]
- [ ] **Export**: self-contained HTML + JSON + CSV issue export from one result;
      PII-like columns masked. [box 8]
- [ ] Run has `run_id`, config version/hash, status, duration, error log, engine version. [box 9]
- [ ] Job runs in the **worker**: UI responsive, observable, **cancellable**. [box 10]
- [ ] Worker crash does not corrupt the store; recovery on restart works. [box 11]
- [ ] **Source files not modified** (sha256 before/after every run). [box 12]
- [ ] `netstat -ano | findstr LISTEN` for the app PID shows **no app-defined
      listener** beyond pywebview's loopback (ADR-005 / ADR-023). [box 13]
- [ ] **Double-click without console** launches (record any SmartScreen/AV
      interference; do not disable AV to hide it — SECURITY §6.2).

### 6a. Upgrade + uninstall — data preservation (SECURITY §6.5)

Mirror the macOS byte-inventory method (`M7_T3_INSTALLER_MATRIX.md` §2a; the
macOS leg used `.benchmarks/m7t5/box16_inventory.py pre|post`):

- [ ] **Snapshot user-data before**: hash + count every file under
      `%APPDATA%\DataProfilingEngine` (the storage default — `DEFAULT_APP_NAME`
      has **no spaces**, `storage.paths`; the *install* dir does use spaces, do
      not conflate them). Read the SQLite files from a **copy** (opening the live
      `-wal` writes `-shm`).
- [ ] **Upgrade**: reinstall with a bumped `/DPRODUCT_VERSION` over the existing
      `$INSTDIR`; confirm prior runs/exports remain present + readable; a
      pre-migration SQLite backup exists; `schema_version` unchanged or additively
      bumped (CONTRACTS §1).
- [ ] **Uninstall** (ARP): removes only `$INSTDIR` + shortcuts + its HKCU ARP key.
      **`%APPDATA%` user data must survive** — the NSIS uninstall section
      deliberately never touches it (`packaging/windows/profiler.nsi`, `Section
      "Uninstall"`). A `-shm`/`-wal` change is not data loss; a changed
      `metadata.sqlite3` or artifact hash is.
- [ ] **Snapshot after + diff** → byte-identical user data (excluding the
      `-shm`/`-wal` nuance above).

---

## 7. Packaged baseline — box 17 Windows (RSS + guarantees)

The `benchmark` module is **not** bundled in the frozen exe; run the CLI in the
same Windows env (this is the packaged-machine baseline, §5.1). Fixtures: reuse
the ladder CSVs if copied to the box (`fixture_paths`), else generate via the
harness generator — check `df`/free disk first (DuckDB spills on the same volume).

```powershell
python -m data_profiling_engine.benchmark baseline
python -m data_profiling_engine.benchmark guarantees
```

- [ ] Record `baseline` wall-clock + `guarantees` (cancellation latency, crash
      recovery) JSONs, each carrying the ADR-021 ceilings block.
- [ ] **RSS on Windows is a `psutil` SAMPLED peak** — every case's JSON carries
      `peak_rss_method = "psutil_sampled_peak"`. Label it as sampled in the
      report, **never as exact** (ADR-015). The macOS `resource` series remains
      the reference curve; the Windows run proves **no-crash completion + the
      sampled RSS trend** (see `WINDOWS_M7_T3_CHECKLIST.md` §6, and
      `M7_T3_INSTALLER_MATRIX.md` §6 risk row for `insufficient_data` → install
      `psutil`).
- [ ] For a full both-OS ladder comparable to `ladder_10m_100m_macos_dev.json`
      (10M ~22 min / 100M ~2h44m on the macOS dev box), decide scope before
      launching: it is a long scheduled run. The minimum to close box 17 Windows
      is the `baseline` + `guarantees` on the packaged Windows machine; a tiered
      ladder is the gold standard.
- [ ] If a finished run's spill scratch reads 0 B and `duckdb_memory()` reads 0
      — that is expected (spill blocks are deleted on completion); the runner's
      `spill_samples_<pass>.log` is the surviving record. Never report a number
      from a log echo alone if the verdict JSON is absent.

---

## 8. Report-back template (paste this filled-in)

Header: **checkout SHA = ______**, `winver = ______`, RAM = ______, free disk =
______, exe size = ______, installer size = ______, build elapsed = ______.

- **§5 markers**: paste every line from `smoke_win.txt`.
- **§6 checklist**: pass/fail per box (1–13 + double-click line), each with a
  screenshot path or log reference.
- **§6a**: the before/after user-data inventory diff (must be byte-identical
  modulo `-shm`/`-wal`).
- **`sales_deals` findings diff** vs the known findings (TESTING §5.1 box 15
  parity — the M4-T1 golden harness pins them).
- **§7**: the `baseline` + `guarantees` JSONs (with ADR-021 ceilings +
  `psutil_sampled_peak` label), and the ladder JSON if a tiered run was done.
- Any workaround, SmartScreen/AV event, or non-green line — state it plainly.

Return all of it to the macOS session. **Do not commit anything from the Windows
box**; the macOS session reconciles and updates `STATE.md` + the §5.1 board.

---

## 9. How GATE MVP gets stamped (after both legs report)

GATE MVP is stamped by the **macOS session**, only when:

1. This packet's Windows legs (§5, §6/§6a, §7) report green → box 16 and box 17
   move from PARTIAL to GREEN on Windows.
2. **AND** the macOS codesign + notarization leg is green (`M7_T3_INSTALLER_MATRIX.md` §3 —
   needs a Developer ID identity; currently blocked on this box).

Until **both** are green, boxes 16 + 17 stay PARTIAL and `docs/TESTING_BENCHMARKS.md`
§5.1 checkboxes remain unticked — the packet is the evidence log, not the tick.
When it lands, the macOS session: updates the per-box board in `STATE.md`
(16/17 status + evidence links), ticks §5.1 in TESTING, and stamps GATE MVP.

> **Gate rule (from `WINDOWS_M7_T3_CHECKLIST.md` §9):** any red → do **NOT**
> stamp M7-T5/GATE MVP; log the failure in `STATE.md` and revise the owning ADR
> (ADR-003/009/010/013) rather than working around it (AGENTS §8).

---

## 10. Known Windows risks (what a failure means)

Carried verbatim in intent from `WINDOWS_M7_T3_CHECKLIST.md` §9 — do not
silently patch these in code:

| Symptom | Likely cause | Action per docs |
|---|---|---|
| `WEBVIEW2_SMOKE_FAILED`, blank window | No WebView2 Runtime | Install Evergreen; confirm the §6.4 bootstrapper path degrades gracefully (no silent hard-crash) |
| `WORKER_SMOKE_FAILED` / exe exits instantly | spawn/freeze bootstrap in frozen child | **Gate failure — revise ADR-009, do not code around it** |
| `ImportError: _duckdb` / `clr` at startup | native-lib / pythonnet collection gap in spec | Report exact error; spec fix happens in the macOS session |
| `SCHEMAS_SMOKE_FAILED` | package data not collected | Fix the spec (data-by-PATH, see the M7-T4a GOTCHA); don't ship |
| RSS `insufficient_data` in `baseline` | `psutil` missing | `uv pip install psutil` (§2); re-run §7 |
| AV quarantines the exe | unsigned onefile heuristic | Record; Authenticode signing via `signtool` is a release-env step, not a repo concern (SECURITY §6.2) |
