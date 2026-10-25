---
title: "M7-T3 — Both-OS Installer Matrix Runbook"
aliases: [M7-T3, Installer Matrix, Release Gate]
tags: [ops, runbook, m7-t3, packaging, release-gate]
status: active
created: 2026-10-25
doc_role: ops-runbook   # execution checklist, NOT a spec. Specs: docs/SECURITY_PACKAGING.md §6, docs/TESTING_BENCHMARKS.md §5.1.
---

# M7-T3 — Both-OS installer matrix (install → launch → job → export → upgrade → uninstall)

Purpose: run the **packaged** release gate (TESTING §5.1) on **both** macOS and
Windows. Per BLUEPRINT Q4 the Windows leg needs a **native physical/virtual
Windows machine** (no container substitute); the macOS leg needs a **Developer ID
identity** for codesign+notarize. Repo-side tooling that does *not* need that
hardware/cert now lives in `packaging/`:

| Artefact | Path | Runs where |
|---|---|---|
| macOS `.app` + `.dmg` build | `packaging/macos/build_app_dmg.sh` | any dev Mac |
| macOS codesign + notarize | `packaging/macos/sign_notarize.sh` | Mac **with** Developer ID (env creds) |
| macOS entitlements | `packaging/macos/entitlements.plist` | consumed by sign script |
| Windows NSIS installer template | `packaging/windows/profiler.nsi` | native Windows (`makensis`) |
| Frozen build spec (per-OS) | `data_profiling_engine.spec` | both (windowed `.app` on mac, onefile `.exe` on win) |

> [!IMPORTANT] What "done" means here (SECURITY §6.6)
> A successful run in the development environment proves nothing about the
> installer. M7-T3 is only **accepted** when the *packaged* app passes the §5.1
> checklist **on each OS**, including install→job→export→**upgrade (data
> preserved)**→**uninstall (data preserved)**. Signing/notarization (macOS) and
> the native Windows run cannot be executed from a non-Windows / unsigned machine;
> they are handed to a release environment / Windows operator below. Do not stamp
> M7-T3 (or MVP) accepted until both legs report green.

## 0. Rebuild from current code (never reuse a stale `dist/`)

`dist/` may predate M5/M6/M7-T2c. Always rebuild first:

```bash
# frontend assets must be current (spec fails fast if missing)
( cd frontend && npm ci && npm run build )
.venv/bin/python -m PyInstaller --clean --noconfirm data_profiling_engine.spec
# or the whole mac track in one shot:
packaging/macos/build_app_dmg.sh
```

## 1. Packaged smoke (freeze-pathway proof — §5.2 markers, both OS)

A windowed `.app` keeps stdout when its inner binary is run **directly** (not via
`open`). Auto-closes in ≤ ~15 s.

```bash
# macOS — windowed onedir .app keeps stdout when the inner binary is run
# directly. DPE_ENABLE_SPIKE_COMMANDS=1 turns on the M0 smoke probes only
# (echo / worker_echo_job); the packaged PRODUCTION path leaves them OFF, so a
# bare `--smoke` correctly reports the two spike commands as "not in the
# allow-list" — set the env var to drive the bridge+worker round-trip.
DPE_ENABLE_SPIKE_COMMANDS=1 \
  dist/DataProfilerEngine.app/Contents/MacOS/DataProfilerEngine --smoke
```
```cmd
:: Windows (run from cmd.exe so stdout is capturable)
dist\DataProfilerEngine.exe --smoke > smoke_win.txt 2>&1
```

All markers must appear:

- [ ] `DUCKDB_SMOKE_RESULT: version=... rows=[(42,)]` — native `_duckdb` collected (ADR-009 top risk)
- [ ] `WORKER_SMOKE_RESULT: job=... status=SUCCEEDED ...` — frozen **spawn** worker round-trip (ADR-006)
- [ ] `BRIDGE_SMOKE_RESULT: ...echo...ping...` — JS↔Python bridge round-trip
- [ ] `PORTS_SMOKE_RESULT: no_listening_sockets pids=[...]` — SECURITY §8.1 self-check (lsof/netstat over app pids; `PORTS_SMOKE_FAILED` = something bound a port — stop and escalate, ADR-005)
- [ ] `SMOKE_OK: window opened and closed cleanly, index=..._MEIPASS/frontend/dist/index.html` — `file://` from bundle (ADR-010)
- [ ] Windows only: `WEBVIEW2_SMOKE_RESULT: runtime_found pv=<version>` (SECURITY §6.4)

## 2. Packaged §5.1 acceptance (the release gate) — both OS

Run against the **installed** app (drag from `.dmg` on macOS; run the NSIS
installer on Windows), not the raw bundle. Every line of TESTING §5.1:

- [ ] Load a dataset via a supported connector — **CSV and Parquet**; no other connector reachable (ADR-011)
- [ ] App shows dataset summary + schema profile; physical vs logical types distinguished
- [ ] Engine produces statistics + distributions + missingness + uniqueness
- [ ] Quality rules run; `PASS`/`FAIL`/`WARN`/`ERROR` distinguished correctly
- [ ] Findings carry evidence + refs to metric/rule; `fingerprint` present (ADR-016)
- [ ] Recommendations do not assert unproven causes
- [ ] Every metric labeled `exact`/`approximate`/`sampled`; sampled marked + seed recorded; none claimed exact (ADR-015)
- [ ] HTML report + JSON from the same structured result; CSV issue export present; HTML self-contained (offline charts)
- [ ] Run has `run_id`, config version/hash, status, duration, error log, engine version
- [ ] Profiling runs in the **worker**, not the UI thread; UI responsive; job observable + cancellable
- [ ] Worker crash does not corrupt the metadata store; recovery on restart works
- [ ] Source files are **not modified** by profiling (checksum before/after)
- [ ] Internal comms use the bridge only — **no HTTP/TCP port bound** — ADR-005; SPA routing works over `file://` (HashRouter, ADR-013). Verify: `lsof -iTCP -sTCP:LISTEN` (mac) / `netstat -ano` (win) shows **no listener** for the app process
- [ ] No local dev server required for the packaged run
- [ ] Unit + integration tests (§3) green on the packaged environment; `sales_deals` fixture yields the known findings
- [ ] **Installer matrix**: clean install → launch → one full job → export → **upgrade** (data preserved) → **uninstall** (data preserved), on both OS
- [ ] Benchmarks documented with recorded baselines (`benchmark baseline` + `benchmark guarantees`, now carrying the ADR-021 ceilings in each JSON)

### 2a. Upgrade / uninstall data-preservation detail (SECURITY §6.5)

- [ ] **Upgrade**: install a newer version over the existing app dir; confirm
      prior runs/reports/exports under the user-data root (`storage.paths`
      default: `~/Library/Application Support` on macOS, `%APPDATA%` on Windows)
      are still present and readable.
- [ ] Metadata migrations run forward on first launch of the new version; a
      pre-migration SQLite backup exists (SECURITY §6.5). `schema_version`
      unchanged or bumped additively (CONTRACTS §1).
- [ ] **Uninstall**: removing the app leaves user data intact (NSIS uninstall
      deletes only `$INSTDIR` + shortcuts + its ARP key). No silent deletion of
      results; removal of user data is an explicit opt-in only.

## 3. macOS track — codesign + notarize (needs Developer ID)

Local (unsigned) build + §1/§2 verification runs on any dev Mac. For a
*distributable* artifact, run in a release environment holding a Developer ID
Application identity — credentials come from the environment and are never
committed (AGENTS §7):

```bash
# NOTE: `hdiutil create/attach/verify` use macOS disk arbitration and must run
# OUTSIDE a restrictive sandbox (a sandboxed shell returns "Operation not
# permitted"); run in a normal user Terminal / release runner.
export DPE_SIGN_IDENTITY="Developer ID Application: <Org> (<TEAMID>)"
export DPE_TEAM_ID="<TEAMID>"
# key-based auth (preferred) OR Apple-ID auth — see packaging/macos/sign_notarize.sh
export DPE_NOTARIZE_API_KEY=... DPE_NOTARIZE_API_KEY_ID=... DPE_NOTARIZE_API_ISSUER_ID=...
packaging/macos/build_app_dmg.sh
packaging/macos/sign_notarize.sh "dist/DataProfilerEngine-0.1.0-macos.dmg"
spctl --assess --type open --context context:primary-signature -vv dist/DataProfilerEngine*.dmg  # Gatekeeper
```

- [ ] `codesign --verify --deep --strict` passes; `notarytool` accepted; `stapler` valid
- [ ] Gatekeeper allows the notarized `.dmg` on a clean Mac (SECURITY §6.3)

## 4. Windows track — operator runbook (native Windows 10/11 x64)

Mirrors `WINDOWS_M0_T5_CHECKLIST.md` for the Phase-0 spike, extended to the full
installer matrix. **The detailed, step-by-step operator version lives in
[`WINDOWS_M7_T3_CHECKLIST.md`](./WINDOWS_M7_T3_CHECKLIST.md)** — run that top to
bottom on the Windows box. Prereqs: Git, Node LTS, **uv**, **Python 3.12** (parity),
**NSIS** (`makensis`), **psutil** (Windows RSS baseline, §6), WebView2 Evergreen
runtime (or the bootstrapper).

```powershell
# 1. env + deps (see WINDOWS_M0_T5_CHECKLIST.md §1-2 for the exact uv steps)
git clone <repo> ; cd new-profiler
uv venv --python 3.12 .venv ; .venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
python -m pytest data_profiling_engine/tests -q          # record pass count vs macOS

# 2. frontend + frozen onefile exe (spec picks edgechromium + clr on win32)
cd frontend ; npm ci ; npm run build ; cd ..
python -m PyInstaller --clean --noconfirm data_profiling_engine.spec
#    -> dist\DataProfilerEngine.exe

# 3. packaged smoke (§1 markers above) -> paste marker lines back

# 4. NSIS installer
makensis /DPRODUCT_VERSION=0.1.0 packaging\windows\profiler.nsi
#    -> DataProfilerEngine-0.1.0-win64.exe

# 5. run the installer on a clean machine, then §2 acceptance checklist
```

- [ ] Installer launches from a **double-click without console** (no SmartScreen/AV
      hard-block; if AV quarantines the unsigned onefile, record it — signing is a
      release-env step with `signtool`, not a repo concern)
- [ ] WebView2 present or the §6.4 warning path is graceful (window doesn't silently crash)
- [ ] Upgrade + uninstall data-preservation (§2a) confirmed
- [ ] `netstat -ano` for the app process shows **no listening port** (ADR-005)

## 5. Report back (closes the gate)

Send per OS: the §1 marker lines, the §2 checklist with pass/fail per line, the
`sales_deals` findings diff, the recorded `baseline`/`guarantees` JSONs (with the
ADR-021 ceilings), and for macOS the notarization status. The macOS session
verifies against TESTING §5.1 and updates `STATE.md` GATE M7 only when **both**
legs are green.

## 6. Known packaging risks (SECURITY §6.2) — how to read a failure

| Symptom | Likely cause | Action |
|---|---|---|
| `ImportError: _duckdb` / DuckDB marker missing | native lib not collected | spec fix (`collect_dynamic_libs`); re-build |
| Worker marker fails / app exits instantly (win) | spawn/freeze bootstrap in frozen child | gate failure — revise ADR-009, don't code around |
| Blank window (win) | no WebView2 runtime | install Evergreen / bootstrapper (§6.4) |
| Gatekeeper blocks on first open (mac) | not signed/notarized | run §3 in a Developer ID env |
| AV quarantines exe (win) | unsigned onefile heuristic | record; sign via `signtool` in release env |
| `baseline` RSS verdict `insufficient_data` (win) | `psutil` not installed (POSIX `resource` absent on Windows) | `uv pip install psutil`; RSS then records as a **sampled** peak (`peak_rss_method="psutil_sampled_peak"`, ADR-015 — never claim it exact) |
