---
title: "STATE — Working Memory"
aliases: [STATE, Progress]
tags: [state, progress, working-memory]
status: live
doc_role: working-log   # NOT a spec. Specs live in docs/. See AGENTS.md §1.
---

# STATE.md — Working Memory (read FIRST every session, update LAST)

> [!WARNING] How to use
> Protocol is defined in `AGENTS.md` §10. Rules of this file: (a) one task ≈ one session; (b) each task line carries spec pointers — open ONLY those sections; (c) never add scope here that isn't in `docs/BLUEPRINT.md`; (d) keep this file under ~150 lines — it is a pointer board, not a journal.

## ▶ CURRENT POSITION

```text
Milestone : M7 — Hardening & Release Gate — M6 UI screens COMPLETE
          (GATE M1 CONTRACTS FREEZE PASSED
          2026-10-24; GATE M2 engine spine PASSED 2026-10-24 — profiler-cli
          end-to-end; GATE M3 persistence MET 2026-10-24 — ADR-008 fully
          landed; GATE M4 reporting COMPLETE dev-mode 2026-10-24 — packaged
          verification deferred to M7/Phase-3; M5 bridge facade COMPLETE
          dev-mode 2026-10-25 — T1 read commands + caps, T2 job manager +
          real profile.start worker, T3 §5.5 progress push; packaged
          bridge/worker verification on both OS deferred to M7/Phase-3)
Task      : **M7-T5 IN PROGRESS — run 2 (2026-10-26) walked the installed
          macOS `.app` end to end, incl. the box-16 uninstall leg (user data
          byte-identical after removal) and **M7-T5c**, which fixed a
          gate-class defect found while driving it: the packaged shell showed
          "Desktop bridge not detected" because readiness was a one-shot
          render-time snapshot taken before pywebview's async injection.
          GATE MVP is NOT stamped: M7-T3's Windows + notarize legs are still
          blocked and boxes 16/17 remain partial. See the per-box board
          under the M7-T5 task line.**
          M6 COMPLETE; M7-T1 DONE; M7-T2 COMPLETE;
          M7-T2c DONE; M7-T4 + M7-T4b DONE; M7-T6 DONE (ADR-023 Option A landed —
          packaged loopback static-file server allow-listed, pywebview pinned);
          M7-T3 macOS-track LANDED (unsigned) — Windows + codesign/
          notarize legs BLOCKED (no Win box, no Developer ID), see M7 section**
          (T2a = the scheduled 10M→100M RSS-curve ladder, TESTING §4.3.3 +
          `benchmark/ladder_10m_100m_macos_dev.json`: verdict **`bounded`**
          (`rss_ratio 5.832 < row_ratio 10`) — 10M 1310.2 s / 677.0 MB, 100M
          9843.7 s / 3948.2 MB, both SUCCEEDED / 0 errors. T2b = cancellation
          latency + crash recovery via `benchmark/cancel_recovery.py` +
          `benchmark guarantees`, TESTING §4.3.2: bounded / 5.48 s,
          consistent / 0.055 s. **§4.3.3 also records the caveat: bounded ≠
          flat** — the joined 200k→100M series grows ≈ `rows^0.766` (~22.5 GiB
          extrapolated at 1B rows), escalated as new task **M7-T2c**, now
          **DONE**: ADR-021 owns the ceilings and TESTING §4.3.4 records the
          bounded re-measure (the ceiling bounds a run but never shrinks it
          below its working set; below that, metrics turn into ERRORs).) M6-T6 done
          (Report View: Findings & Recommendations §4.2–4.3 + Export §3.5).
          **Required a contract change**:
          added the §5.2 read command `recommendations.get` (ADR-020) because a
          Finding carries only `recommendation_ids` — no existing command returned
          the §4.3 recommendation *bodies* (they lived only in the `profile_json`
          artifact), the same gap class ADR-019 closed for columns; raised per AGENTS
          §8 and approved before coding. M5 + M6-T1..T6 landed + M7-T1 + M7-T2.
          866/866 pytest green, 2026-10-25 (no frontend change this task).
GATE M4   : reporting-layer tasks T1-T4 COMPLETE (706/706 pytest, tsc clean,
          golden byte-stable). MVP acceptance is on the PACKAGED build
          (BLUEPRINT §5 Phase-1 gate / §6), not dev mode — do not stamp M4
          "accepted" until the packaged run is verified in M7/Phase-3.
NEXT      : M7-T5 run 2 — **GATE MVP: every box of TESTING §5.1 green on
          packaged builds**. OPEN docs/TESTING_BENCHMARKS.md §5.1 (+ §5.4
          results) and the per-box board under the M7-T5 task line.
          RUN 1 (2026-10-26, this box) = packaged macOS smoke only: `dist/`
          rebuilt at HEAD `519914e`, `--smoke` under
          `DPE_ENABLE_SPIKE_COMMANDS=1` green on all five macOS markers
          (`PORTS_SMOKE_RESULT: only_pywebview_loopback` 127.0.0.1:45054 +
          `SMOKE_OK … _MEIPASS/frontend/dist/index.html`), 925 passed pytest.
          Boxes 13/14/15 stamped from run 1; the interactive walk on the
          INSTALLED app (boxes 1–12) landed in run 2, and box 16's macOS
          install→launch→job→export→upgrade→**uninstall** legs are all green
          (uninstall = 2026-10-26, data byte-identical, see the run-2 notes).
          M7-T5c (2026-10-26) removed the UI-side blocker for boxes 7/8/11:
          bridge readiness is now event-driven and verified on a rebuilt `.app`.
          STEP 7 (2026-10-26) = box 7 EXECUTED on the installed app: packaged
          `sampled`+seed run `095f3987…` (2M rows, reservoir/100000/seed
          424242) labeled 67 `exact` + 9 `sampled` + 1 `not_computed`, every
          sampled leaf carrying seed+strategy+window, UI showing
          `Sampling seed 424242` + `sampled` badges. It also exposed ONE
          remaining honesty gap: `Uniqueness.is_unique_candidate` is a bare
          bool (no provenance slot) derived from the SAMPLE view → a sample
          presented as a fact. STEP 7b (2026-10-26) = that gap FIXED per the
          user's choice (full-relation derive, no §3.2 change): the profiler
          now abstains for any non-EXACT decision and `orchestration.rollup`
          stamps the bool from the same exact count(DISTINCT)/count(col) scan
          that resolves `primary_key_candidates`. Rebuilt + reinstalled
          (`63257fbff18fba71`) + packed-smoke 6/6 + **box 7 GREEN** on two
          packaged runs (sampled and approximate) proved against independent
          DuckDB truth. STEP 8 (2026-10-26) = box 11 EXECUTED on the installed
          app, both legs: (a) worker SIGKILLed mid-run (`job_3819909dcb44`,
          killed pid 67356 at step 4/14) → store mirrored
          FAILED|WORKER_CRASHED ≤0.5 s, Progress screen rendered the badge +
          `WORKER_CRASHED — see the §8 registry` banner, UI stayed live;
          (b) whole HOST SIGKILLed with a job RUNNING (`job_60ebe6e6173e`) →
          relaunch + first `profile.start` reconciled it to
          FAILED|WORKER_CRASHED (§6.3 recovery-on-restart, lazy via
          `runtime.get_profile_manager`). Both: 0 orphan `runs` rows,
          `pragma integrity_check`+`foreign_key_check` ok, fixture sha256
          unchanged, and a post-crash full run SUCCEEDED 14/14
          (`job_c6f25d80cfa1` / run `5f4f6356…`). Evidence
          `.benchmarks/m7t5/box11_*.png` + `box11_killwatch.log` /
          `box11_killhost.log`. **Box 11 GREEN → board 15 GREEN · 2 PARTIAL
          (16,17) · 0 OPEN.** NEXT (2026-10-26, Windows leg VERIFIED): the
          **Windows boxes 16 + 17 are verified green by this macOS session** —
          the automated report was cross-checked against `facade.py`/`Report.tsx`/
          NSIS + the byte-identical golden, and the **box-16 GUI screenshot
          addendum closed the last visual caveat** (13 live installed-GUI captures,
          see the box-16 row). The ONLY things still gating MVP now:
          (1) **box 17's 10M→100M packaged ladder** to confirm the ADR-021 plateau
          — the one runnable-here action (long, check `df -h` first; reuse the
          retained `.benchmarks/ladder` fixtures via `fixture_paths`); and
          (2) **macOS codesign + notarization** (Developer ID — blocked on this
          box). GOTCHA (F-1, box 2, resolved — do NOT re-open as a defect):
          `dataset.inspect` emits `physical_type` only pre-run and joins
          `logical_type`/`semantic_type` only once a run exists (facade.py:345–361,
          spec-correct); the schema *table* has no Physical column, but the
          per-column detail header renders `#N · physical <type> · <logical> ·
          <semantic>` (Report.tsx:849–852) — that is where physical-vs-logical is
          proven. My addendum's "Inspect shows physical vs logical" wording was
          imprecise, not the app. GOTCHA:
          `recover_interrupted_jobs` is lazy — restarting the app alone does
          NOT flip an orphan RUNNING row until the first bridge call that
          materialises the manager (e.g. next `profile.start`).
          Still owed: Windows track, codesign/notarize, box 17 both-OS
          baselines. Non-gate UI follow-ups (user-logged 2026-10-26) —
          BOTH CLOSED 2026-10-26: (a) Report `Unique candidate yes|no` now
          carries a source hint ("exact full-relation …", names the sampled
          distinct_count's method) under the Uniqueness block in `Report.tsx`;
          (b) Browse cancel-hang: pywebview 6.2.1/Cocoa can leave the
          `dialog.open_file` promise unsettled when the NSOpenPanel is
          cancelled, latching "Opening…" forever — `DatasetSelection.tsx`
          `handleBrowse` now has a 15 s watchdog + `browseEpoch` ref guarding
          late settles (late genuine picks still fill the path). tsc clean,
          vite build clean, 947/947 pytest green. Re-verified on Windows: the
          `fe70bbe` build (which carries these) hit 947/947 + a byte-identical
          golden on the Windows box 2026-10-26, so the packaged path is now
          proven on both OS (macOS packaged rebuild still pending).
          Reconciliation edits THIS session (committed): packet authored +
          corrected (user-data root is `%APPDATA%\DataProfilingEngine` NO spaces
          per `storage.paths.DEFAULT_APP_NAME`, install dir uses spaces; +
          makensis-PATH, comtypes, cmd-trailing-space `set` gotchas), and
          `benchmark/harness._total_ram_gb` gained a win32 `psutil` branch (was
          None on Windows; macOS/Linux paths byte-unchanged so the reference
          curve is untouched — 22 harness tests green).
          ADR-023 is now **Accepted (Option A)** — the §8.1 "no app-defined port
          bound" box can be stamped on the packaged build with that scoping
          (`--smoke` emits `PORTS_SMOKE_RESULT: only_pywebview_loopback host=127.0.0.1
          port=<n> …`; any other listener still FAILs). M7-T6 landed: `main._run_ports_smoke`
          refactored into pure helpers (`_LOOPBACK_HOSTS`/`_split_host_port`/
          `_parse_listener_lines`/`_pywebview_loopback_endpoint`/`_select_port_offenders`)
          that allow-list exactly pywebview's live loopback `(host,port)`; `+10 tests
          in tests/test_ports_smoke.py`; `requirements.txt` pins `pywebview>=6.2,<7`;
          ADR-005/SECURITY §8/TESTING §5.1 + both installer runbooks amended to the
          scoped wording. GOTCHA (corrected): the `--smoke` worker/bridge probes
          (`echo`/`worker_echo_job`/`job.get_status`) are **NOT stale — do not
          retire them**; they are intentional M0 spike commands registered only
          behind `DPE_ENABLE_SPIKE_COMMANDS=1`, so a bare `--smoke` correctly
          rejects them as `BRIDGE_COMMAND_UNKNOWN` and the runbook drives the
          positive bridge+worker round-trip with that env set (STATE's earlier
          "retarget/retire the probes" note was wrong).
          ⛔ The gate CANNOT pass from this box yet: the Windows installer matrix
          + GUI walk are now VERIFIED green (2026-10-26, `fe70bbe`, incl. the 13-
          shot screenshot addendum), but macOS **codesign + notarization** is still
          blocked (Developer ID identity required — BLUEPRINT Q4, AGENTS §7), and
          box 17's 10M→100M packaged ladder is still owed (runnable here, long).
          The Windows box already rebuilds `dist/` from the checkout (`fe70bbe`), so
          the PORTS marker is in the shipped Windows binary; the macOS distributable
          still needs the sign/notarize legs per `docs/M7_T3_INSTALLER_MATRIX.md`
          §3. Test: .venv/bin/python -m pytest data_profiling_engine/tests -q
Blocked by: macOS codesign/notarization needs a Developer ID (the Windows M7-T3
          leg is VERIFIED green 2026-10-26); box 17's 10M→100M packaged ladder is
          still owed. Do NOT stamp M7-T5/GATE MVP before those run. GOTCHA (disk): volume sits at 97 % full — a 100M CSV is 6.2 GB
          and DuckDB spill needs room on the SAME volume. The ladder fixtures
          (`.benchmarks/ladder/csv_10000000_std.csv` + `csv_100000000_std.csv`,
          6.8 GB, gitignored) are **deliberately RETAINED for the M7-T2c
          re-measure** (user decision 2026-10-25) — reuse them via the harness's
          `fixture_paths`, don't regenerate; check `df -h` before adding any new
          tier, and expect spill to eat a few more GiB.
GOTCHA (bg run): the 100M bounded pass FINISHED 16:21:57Z 2026-10-25 →
          `.benchmarks/ladder/bounded_100m_top.json` (stdout `top_run.log`, spill
          sampler `spill_samples_top.log`). If that JSON is absent a pass did not
          finish — never report a number from the log's echo line alone.
GOTCHA (spill evidence): a finished DuckDB run DELETES its spill blocks, so the
          scratch dir reads 0 B afterwards and `duckdb_memory()` reads 0 too. The
          100M pass really spilled 87.7 MiB (sampler peak, 1734 samples) while
          leaving 0 B on disk; the runner's `spill_samples_<pass>.log` is the only
          surviving record — `benchmark watch --pass-name` now prefers it and
          reads `ceilings.temp_directory` from the verdict (its own process
          default pointed at a different, empty dir).
GOTCHA (DuckDB 2.0.0): `duckdb_memory()` reports **live** buffers only (reads 0
          once a query ends) → a spill must be measured by sampling the scratch
          dir; and `median`/`quantile_cont` each need a fixed 128 MiB block, so a
          `memory_limit` below ~512 MB converts those metrics into ERRORs instead
          of shrinking the profile.
Session notes: M7-T5 (run 1) — packaged macOS smoke evidence, GATE NOT stamped
               2026-10-26 @ `519914e`, working tree clean (shipped binary ==
               HEAD). `dist/` was rebuilt because the previous bundle was 03:05
               while the engine changed at 09:46 (M7-T3 Windows commit touched
               storage/reporting): `.venv/bin/python -m PyInstaller --clean
               --noconfirm data_profiling_engine.spec` → EXIT=0 in 247 s (log
               `.benchmarks/m7t5/pyinstaller_build.log`). Frontend reused from
               `frontend/dist` (last `src` change 02:01 < dist 03:01) instead of
               a full `npm ci` rebuild. DMG re-cut at 10:16
               (45 335 447 B, `hdiutil verify` VALID) and is STILL UNSIGNED —
               codesign/notarize belongs to `packaging/macos/sign_notarize.sh` in
               a release env (0 Developer ID identities here).
               Packaged `--smoke` under `DPE_ENABLE_SPIKE_COMMANDS=1` → EXIT=0,
               all five macOS markers green (DUCKDB 2.0.0 rows=[(42,)] / WORKER
               `status=SUCCEEDED` 4/4 / BRIDGE `status=ok` / PORTS
               `only_pywebview_loopback` 127.0.0.1:45054 pids=[28870,28880] /
               SMOKE_OK `file://` index) — log
               `.benchmarks/m7t5/smoke_packaged.log`; `WEBVIEW2_SMOKE_*` is
               Windows-only ⇒ N/A here.
               Full suite at this HEAD: **925 passed in 367.77 s** (log
               `.benchmarks/m7t5/pytest_head.log`; +5 over the 920 recorded after
               M7-T6, from the two case-fixture commits).
               §5.1 board = **3 GREEN · 2 PARTIAL · 12 OPEN**; GATE MVP NOT
               stamped because the interactive walk on the INSTALLED app was not
               run and M7-T3's Windows + notarize legs remain blocked.
               GOTCHA (corrected): `hdiutil create/verify` DOES work in this agent
               sandbox — what failed before was the `mktemp -d` staging dir in
               `/var/folders` (a write outside the workspace). Stage the dmg
               source INSIDE the repo (`packaging/macos/build_app_dmg.sh` still
               uses `mktemp -d`, so it needs an unsandboxed shell).
               GOTCHA (sandbox deletes): the 99 MB `.benchmarks/m7t5/dmg_stage`
               copy used for the dmg survived `rm -rf` (deletes were refused in
               this sandboxed shell) — remove it by hand; it is gitignored.

Session notes: M7-T5 run 2 (in progress) — **GATE-CATCHING DEFECT FOUND ON THE
               INSTALLED APP, now fixed.** The user's first packaged
               `profile.start` (TESTING §5.1 box run) returned
               `BRIDGE_REQUEST_INVALID: handler failed: KeyError`. Root cause
               verified, not guessed: `Contents/Frameworks/data_profiling_engine/`
               shipped `reporting/` but **no `schemas/`**, so
               `schemas.RAW_SCHEMAS` (built by globbing the package dir) was
               EMPTY and `S.validate("dataset_config.schema.json", …)` raised
               `KeyError` → the facade's catch-all reported it as
               `BRIDGE_REQUEST_INVALID`. `dataset.inspect` still worked because
               only `profile.start` validates a §9 config; the frozen
               `--smoke` never validated anything either, so M7-T3's smoke was
               green on a binary that could not start a single job. Fix (spec
               `datas` + a marker that would have caught it):
               `data_profiling_engine.spec` now collects
               `schemas_datas = collect_data_files("data_profiling_engine.schemas",
               includes=["*.schema.json"])`; `main._run_schemas_smoke()` prints
               `SCHEMAS_SMOKE_RESULT: count=13 required=13 probe_errors=4` (and
               `SCHEMAS_SMOKE_FAILED` twice over: missing files, or a registry
               that loads but does not reject an empty document); new
               `tests/test_packaging_assets.py` (7 tests) fails if ANY runtime
               package-asset dir under `data_profiling_engine` is absent from the
               spec (bench/ baselines exempt) — the generic form: a new asset dir
               can no longer be forgotten. Rebuilt (`EXIT=0`, 131 s,
               `pyinstaller_build_run2.log`), upgraded over the installed app
               (13 schemas present, user data + `schema_migrations=2` intact →
               also the §6.5 upgrade leg), re-smoked: **6 markers green**
               (`smoke_installed_run2b.log`). Docs updated: SECURITY §6.2 risk
               row, both installer runbooks' ¶1 marker lists. GOTCHA: a
               dev-mode/suite-green bridge handler proves nothing about the
               frozen app — any runtime read of *package data* must have a
               `--smoke` marker, because `KeyError` becomes a generic
               `handler failed`.
               **Second defect (this run, fixed):** the first packaged job came
               back `SUCCEEDED` while the Progress screen read
               `5/6 steps · 83 % · reporting:csv`. Root cause in
               `worker/profile_job.py`: the 6th boundary `persist:metadata`
               existed as a constant but was NEVER emitted — `_progress(5,
               _STEP_CSV)` → `save_run()` → terminal, so a finished job kept the
               pre-persist progress forever. Fixed by emitting
               `_progress(6, _STEP_PERSIST)` after `save_run` (progress is
               monotonic; the terminal is the only state change left); +1 test
               `test_succeeded_job_reports_every_step` (live slot AND the store
               mirror `jobs.progress_completed`), 38 passed across
               profile/worker/cancel-recovery. NOTE: the fix needs a rebuild to
               reach the installed app — done together with the step-3/4 runs.
               **Third defect (this run, fixed):** the step-3 big run (2 M rows
               / 122 MB) exposed one gap with two faces. (a) **progress froze**
               at `1/6 · 17 % · profiling:validate_config` for the whole
               248 s compute pass — the engine spine was one blocking call with a
               single `profiling:metrics` boundary. (b) **cancel was slow**: the
               flag was only read at those two outer boundaries, so
               `profile.cancel` on `job_6d03573bd0ba` took **4 min 18 s** to
               reach CANCELLED (it did get there; nothing hung, and it wrote no
               run row). CONTRACTS §6.5 asks for exactly what was missing —
               "checks a cancel flag at task/step boundaries **and (for DuckDB)
               interrupts the active query**" — and TESTING §5.1 asks for a job
               that is "observable + cancellable", so this was an MVP miss, not
               a Phase-3 nicety (the old docstring wrongly filed it as Phase-3).
               Fix: (1) `orchestration.run_profiling_pipeline` /
               `run_dataset_profiling` / `rollup.profile_dataset` take an optional
               `checkpoint(label)` called **before** every pass (discover, plan,
               the three metric families, quality, findings, recommendations,
               dataset_metrics) — defaults keep the CLI unchanged; (2)
               `worker/profile_job.py` now owns the DuckDB connection, feeds its
               14-step ladder (`PROFILING_TOTAL_STEPS = len(_STAGE_LADDER)`; the
               manager's QUEUED slot now defaults to the same constant instead of
               a stale literal `6`) from those checkpoints, and runs a watchdog
               thread that calls `DuckDBPyConnection.interrupt()` 0.2 s after a
               cancel (measured: **20 ms** to abort a running query), mapping
               `InterruptException` to **CANCELLED** while a real failure with no
               cancel pending stays FAILED. **GOTCHA — the interrupt must be
               RE-ARMED, not fired once:** `engine/metrics/profiler.py` catches
               `duckdb.Error` per metric and records `METRIC_NOT_COMPUTED`, so a
               single `interrupt()` stops exactly ONE query and the pass scans
               on — the first implementation did this and the measured cancel
               latency was still **114 s** on the 2 M-row fixture (it did stop at
               the next stage checkpoint, so the job ended CANCELLED, just
               slowly). While the flag stands the watchdog now re-interrupts each
               poll, so every following query aborts in ms and the pass unwinds:
               re-measured **6.9 s** from `cancel_job()` to CANCELLED
               (`.benchmarks/m7t5/cancel_probe_run2.log`; probe cancels mid-
               `statistics_missingness`, ends CANCELLED at 5/14, no run row).
               Residual cost is the remaining metrics of the pass unwinding, not
               the data size; a per-column checkpoint inside the profilers would
               cut it further and is the obvious next refinement, not a blocker.
               +7 tests in `test_worker_cancel_progress.py` (stage order,
               abort-before-downstream, mid-query cancel → CANCELLED, interrupt
               without cancel → FAILED, real interrupt latency, ladder coverage,
               re-arm);
               full suite **940 passed** (`.benchmarks/m7t5/pytest_run2e.log`).
               Rebuilt + reinstalled: `smoke_installed_run2e.log` 6/6 markers.
               **Verified from that first packaged job** (run
               `0aa42cd5-c253-471a-bac1-91c07d9f6512`, `job_a8f1fcdbbfa6`,
               `standard`, 3543 ms, 0 errors, source =
               `tests/fixtures/golden/sales_deals.csv`): 3 artifacts on disk
               (`report.html` 4.8 MB, `profile.json` 28 KB, `issues.csv` 353 B);
               HTML self-contained (only Plotly/OSM *attribution hrefs*, no
               `src`/`@import`/`url(http…)`); `issues.csv` = the 2 FAIL checks
               with `evidence_ref` refs; 5 findings, 5 distinct
               `sha256` fingerprints, every finding carrying `evidence_refs`
               (`metric:`/`rule:`), `scope.affected_records`, `confidence`,
               hypotheses explicitly `status=unverified` + `suggested_probe`
               (box 6); 5 recommendations with `do_not` guardrails; checks =
               `deal_id_unique FAIL` / `value_not_null FAIL` / `stage_domain
               PASS`; provenance labels `exact` 35 + `not_computed` 1 (with a
               `reason`, ENGINE §3 out-of-range needing a declared bound);
               physical vs logical types per column (VARCHAR/string,
               DATE/temporal_date, BIGINT/numeric_currency) and
               missingness+uniqueness on all 5 columns, `distribution` on the
               numeric one, `categorical`/`temporal` where applicable; run row
               carries run_id / config_hash `sha256:9e9c673c…` / config_version 3
               / engine_version 0.1.0 / duckdb 2.0.0 / duration_ms / status;
               **source unmodified** — sha256 of the fixture still
               `9b9cf013…` == the run's recorded `source_snapshot.content_hash`,
               mtime/size unchanged (box 12). Still owed: UI-acked exports, the
               Parquet leg + `method=sampled` + seed (step 2), cancel/crash
               (steps 3–4), packaged RSS baseline (box 17).
               **Fourth defect (found while driving the installed UI, fixed):** the
               Report page rendered `Schema & Column Profiling` as
               `BRIDGE_REQUEST_INVALID: handler failed: ProgrammingError`. Not a
               payload problem: `MetadataStore` opened sqlite3 with the default
               `check_same_thread=True` while `bridge.runtime.get_metadata_store()`
               hands ONE process-wide store to both the pywebview `js_api` dispatch
               thread and the job-manager monitor — the thread that opened it owns
               it, so any later statement from another thread raises
               `sqlite3.ProgrammingError: SQLite objects created in a thread can only
               be used in that same thread`, and the facade's §5.3 boundary reports
               it as that generic message. Proven in-process before touching code:
               `.benchmarks/m7t5/store_thread_probe.py` (main-thread query ok,
               second-thread query raises exactly that). Fix:
               `sqlite3.connect(..., check_same_thread=False)` (+ `schema_version` /
               `migration_history` under the RLock); every statement already goes
               through `_query`/`_write_tx` under `self._lock`, so the shared handle
               stays serialised. +1 test
               `test_one_store_serves_calls_from_another_thread` (65 passed in
               `test_storage_metadata.py`). GOTCHA: `handler failed: <Driver>Error`
               is a plumbing defect, never a payload one — and a read handler that
               passes in dev can still fail packaged, because the dev process often
               happens to keep one thread and the real one does not.
               **Computer use (this session):** the agent CAN drive the installed
               app. `System Events` has assistive access now: AX tree read, `keystroke`,
               and `click at {x,y}` all work; the window sits at 230,65 (800×600) so
               screenshot coordinates are click coordinates. Driver:
               `.benchmarks/m7t5/ax.js` (`settext` writes the AXTextField value and
               React picks it up, `text` dumps screen text) + `screencapture -x` into
               `.benchmarks/m7t5/gui_*.png`. GOTCHA: a full-tree walk (`dump`, `press`)
               HANGS for minutes on content-heavy pages (the 8-column preview table) —
               System Events does one IPC per element; use `click at` + screenshots
               there and keep `text` for assertions. **Verified after the fix on the
               installed build** (`gui_report_small*.png`): Report → `Schema & Column
               Profiling` renders the 5-column table (deal_id/created_at/stage/…,
               logical+semantic type, missing 0/40, distinct counts), and the same
               page shows box 6/7 evidence in the UI — scorecard `PASS 1 · FAIL 0 ·
               WARN 0 · ERROR 0`, the ``stage_not_null`` check row, and findings with
               `fingerprint: sha256:6ede1d01…`, `column stage · 3 affected records`,
               FACTS / INTERPRETATION / HYPOTHESES (UNVERIFIED + probe) /
               RECOMMENDATIONS (`do_not` guardrail + `evidence:` ref) kept apart.
               **Fifth defect (packaging, fixed):** that rebuild was the first
               `--clean` build done from the tool sandbox, and it produced a bundle
               with **no** `schemas/` at all — `pyinstaller_build_run2f.log` carries
               `WARNING: collect_data_files - skipping data collection for module
               'data_profiling_engine.schemas' as it is not a package`, i.e. the very
               first defect's fix depended on the analysis interpreter's import
               system and could silently collect nothing (the packaged smoke caught
               it: `SCHEMAS_SMOKE_FAILED … 13 missing`). The spec now maps the
               directory literally (`schemas_assets = (str(PROJECT_ROOT /
               "data_profiling_engine" / "schemas"), "data_profiling_engine/schemas")`,
               same shape as `reporting_assets`). Verified: rebuild `BUILD_EXIT=0`,
               `spec_not_a_package_warnings=0`, `schemas_in_bundle=13`,
               `DPE_ENABLE_SPIKE_COMMANDS=1 --smoke` → 6/6 markers
               (`smoke_installed_run2g.log`). GOTCHA: name package data by PATH in the
               spec, never via `collect_data_files(<pkg>)`, and always run the packaged
               smoke with `DPE_ENABLE_SPIKE_COMMANDS=1` — a bare `--smoke` now reports
               `BRIDGE_COMMAND_UNKNOWN` for `echo`/`worker_echo_job` by design.
Session notes: M7-T4b DONE — native open-file dialog on Dataset Selection
               (**ADR-022 / CONTRACTS §5.2 + §8 contract change**, user-approved
               before coding per AGENTS §8; the "revisit in M6-T2" text-input TODO
               is now closed). Added `dialog.open_file` — the first **host-served**
               §5.2 command (every other read is store/connector-backed). Pattern
               mirrors the §5.5 push-sink: `runtime.configure_file_dialog(provider)`
               holds one duck-typed `Callable[[], Optional[str]]`; `main.py`
               `_window_file_dialog(window)` wires it to
               `window.create_file_dialog(webview.OPEN_DIALOG, file_types=SOURCE_FILE_
               TYPES)` after the window exists — the bridge owns NO WebView (so no
               `webview` import crosses into `bridge/`). Payload is EMPTY
               (`extra="forbid"`) so no JS input reaches the dialog (SECURITY §3);
               filters hard-coded host-side to CSV/Parquet (ADR-011), single-select.
               Cancel → honest `ok` `{path: null}`; no registered provider (headless/
               test) → typed `DIALOG_UNAVAILABLE` (§8 row, retryable=false), never a
               silent no-op. Reads no file, grants no scope — downstream
               `dataset.inspect`/`profile.start` still apply the path allow-list.
               Files: `bridge/runtime.py` (provider slot, NOT reset by
               `configure_runtime` — same lifetime rule as `_push_sink`),
               `bridge/envelope.py` (`DialogOpenFilePayload`), `bridge/facade.py`
               (`_dialog_open_file_handler` + COMMANDS), `errors.py` +
               `bridge/errors.py` (`DIALOG_UNAVAILABLE`), `main.py` (wiring),
               `frontend/src/types/contracts.ts` + `bridge/api.ts`
               (`bridgeSelectSourceFile`) + `pages/DatasetSelection.tsx` (Browse…
               btn). GOTCHA: `test_registry_covers_all_doc_codes` asserts
               `_DOC_RETRYABLE` keys == every `ErrorCode` — a new §8 code MUST add a
               `_DOC_RETRYABLE` entry in `test_bridge_envelopes.py` in the same
               change. Tests: exact-set +`dialog.open_file`, `TestDialogOpenFile`
               (5). 910/910 pytest green, tsc clean, vite build clean 2026-10-26.
Session notes: M7-T2c DONE — own the DuckDB ceilings, then re-measure
               (ADR-021 + TESTING §4.3.4). New `duckdb_session.py` is the ONLY
               place a connection opens: `connect()` installs and then READS
               BACK `memory_limit = min(4 GiB, 25 % RAM)`, an owner-only (0700)
               `temp_directory` under `storage.paths.default_temp_root()` (the
               inherited default was the RELATIVE `.tmp` — a SECURITY §2 hazard
               under PyInstaller), and an 8 GiB spill cap; overrides are env
               (`DPE_DUCKDB_MEMORY_LIMIT` / `_TEMP_DIR` / `_MAX_TEMP_DIR_SIZE`,
               blank = unset) or kwargs, deliberately NOT §9 dataset config, so
               no contract/`config_hash` change. All 5 bare connect sites routed
               through it (`connectors/base`, `orchestration/rollup`,
               `orchestration/__init__`, `bridge/facade`, `benchmark/generator`)
               plus a source-level guard test that fails CI on a new bare
               `duckdb.connect()`. Two gotchas found by MEASURING, not assuming:
               (1) a 256 MB ceiling makes `median`/`quantile_cont` raise
               `OutOfMemoryException: failed to allocate data of size 128.0 MiB`,
               so the floor is DERIVED (`2 × threads × 128 MiB`, clamped
               [512 MiB, 4 GiB] → 1 GiB on this 4-core box) and the default is
               additionally held under 90 % of RAM; (2) `duckdb_memory()` reads 0
               after a query, so spill is measured by sampling the scratch dir
               every 5 s (`.benchmarks/ladder/run_pass.sh`). Measured at 10M on
               the retained fixture: 4 GiB → 675.1 MiB / 1218.8 s / 0 err;
               1 GiB floor → 631.6 MiB / 1251.2 s / 0 err; 256 MB → 375.7 MiB /
               914.0 s + 146.3 MiB spill but **28 ERRORs**. Reading: the ceiling
               BINDS (it spills) but never shrinks a run whose working set is
               under it (631.6 / 675.1 vs 677.0 unbounded = variance), and the
               tightest pass is fastest only because it skipped work. Therefore
               `evaluate_rss_plateau` (b ≤ 0.25 and inside the ceiling) is a
               DIAGNOSTIC and `evaluate_rss_boundedness` stays the binding gate.
               Tracked copy `benchmark/bounded_ceilings_macos_dev.json`.
               THEN the top of the ladder (100M, retained fixture, shipped 4 GiB):
               8847.8 s / **3724.0 MiB** / **87.7 MiB spill** / 0 err SUCCEEDED —
               vs unbounded 3948.2 MiB ⇒ −5.7 % RSS, still `rows^0.742` (0.766
               unbounded), i.e. the ceiling is REACHED (90.9 % of 4 GiB) but not
               decisive, because the working set still fits under it. Full-series
               (200k→100M) fit: 0.599 bounded vs 0.607 unbounded.
               Open question for a future decision (NOT decided): a ceiling BELOW
               the 100M working set (e.g. 1 GiB) is the only thing that flattens
               the curve, at a cost of ~3 GB spill — would the plateau gate then
               pass, and is that wall-clock worth it?
               866/866 pytest green 2026-10-25 (no frontend change).
               **→ NEXT: M7-T3** (rebuild the stale `dist/` binary first).
Session notes: M7-T2c helper — `benchmark watch --pass-name <name> --rows <N>`
               (the user asked for a % -done indicator on the live pass). Same
               read-only file as the ladder watcher: verdict JSON = done, live
               `measure_bounded.py --rows N` = running, child
               `*_bounded.measure.json` WITHOUT a verdict = **stalled** (a dead
               driver is never reported as finished), else not_started; exit 0/1/2
               like `watch_ladder`. Estimate = the SAME tier's measured wall-clock
               from the M7-T2a ladder (`csv_<N>_std.measure.json`), basis
               `reference`; with no reference it prints `-`, never a fabricated %.
               It also prints the live size of the DuckDB scratch dir — the only
               direct signal that the ADR-021 ceiling is binding mid-run — and
               resolves that path WITHOUT creating it (a monitor must not change
               what it observes). 13 pass-mode tests in `tests/test_benchmark_watch.py`
               (24 total there, 42 with the harness file). Live log for this session:
               `.benchmarks/ladder/watch_top.sh` → `watch_top.log` (1 snapshot /
               5 min, exits when the pass ends).
               FOUND BY USING IT: the first version reported "0.0 MiB spilled" for
               a pass that had spilled 87.7 MiB — it probed its OWN process
               default instead of the pass's `ceilings.temp_directory`, and the
               emptied-after-close scratch dir reads 0 B anyway. Both fixed: read
               the recorded dir, and prefer the sampler log when one exists.
Session notes: M7-T2a DONE — the 10M→100M RSS-curve ladder ran 14:46→18:04
               local (3 h 18 m wall incl. fixture generation) on
               `.benchmarks/ladder/`, verdict **`bounded`**:
               10M = 1310.2 s / 677.0 MB / 612.4 MB source;
               100M = 9843.7 s / **3948.2 MB** / 6219.0 MB source; both
               `SUCCEEDED`, 0 per-task errors; `rss_ratio 5.832 < row_ratio
               10.0`. Scope decided at execution time per the old NEXT
               delegation: **10M + 100M only** (§4.3 bullet 1 literally says
               "10M vs 100M"; the verdict needs ≥ 2 points), **--no-degrade**
               (degrade already in §4.3.1; a 100M pair = 2 more full passes),
               workload knobs **match §4.3.1** (--cardinality high
               --missingness 0.12 --distribution skewed --columns 8 --format
               csv) so the series joins into one curve. Curated copy now
               tracked at `benchmark/ladder_10m_100m_macos_dev.json` (raw
               scratch stays in gitignored `.benchmarks/`); verdict + honest
               reading recorded in **TESTING §4.3.3**. **THE FINDING:** bounded
               is NOT flat — 94.0 → 169.5 → 677.0 → 3948.2 MB across
               200k → 1M → 10M → 100M ≈ `rows^0.65` (per-decade ×1.80/×4.0/
               ×5.83), which extrapolates to ~17 GB at 1B rows, i.e. OOM on a
               16 GB box. Verified cause candidate: every connect site is a
               bare `duckdb.connect()` (no `memory_limit`/`temp_directory`
               anywhere), so DuckDB may take ~80 % of RAM as its buffer pool —
               streaming is real (no Python materialisation) but the memory
               ceiling is unowned. Escalated as **M7-T2c** rather than stamping
               the §4.2 "stays flat" claim proven. GOTCHA (disk): the volume was
               already 96 % full — a 100M CSV is 6.2 GB and DuckDB spill wants
               room on the same volume; check `df -h` before scheduling the tier
               ladder. Both ladder fixtures are RETAINED (user decision
               2026-10-25) so M7-T2c re-measures without regenerating 6.8 GB.
Session notes: M7-T2a helper — `benchmark watch`, the % -done progress
               indicator the user asked for mid-run ("buat indikator progress
               pooling berupa persentase done"). `benchmark/watch.py` is
               READ-ONLY (never touches the measured process); exit 0 finished
               / 1 in flight / 2 no live `baseline` process (crash is reported
               as not-running, never as done). There is NO step signal to read
               — `profile_dataset` takes no progress callback, only the §5.5
               worker job emits steps — so it reconstructs stage boundaries from
               the harness's own on-disk mtime chain (`{label}.{fmt}` →
               `{label}.config.json` → `{label}.config.{mode}.json` →
               `{label}.measure.json`) plus the parent's `ps` age, and
               self-calibrates s/row + s/byte from stages that already finished
               (fallback rates = the §4.3.1 baseline). Every unfinished number
               carries `basis=estimate|extrapolated`; a stage with no evidence
               yields `-`, never a fabricated percentage (ADR-015 discipline
               applied to the monitor too). CLI: `python -m
               data_profiling_engine.benchmark watch --workdir
               .benchmarks/ladder --sizes 10000000,100000000 [--interval 120]
               [--json]`. GOTCHA: stdout is block-buffered when redirected — the
               loop prints with `flush=True` and is run as `python -u`,
               otherwise a progress log stays empty for tens of minutes. The
               accuracy check: it estimated 3 h 38 m for the 100M pass at 17:24
               and the pass actually took 2 h 44 m, so treat the ETA as ±20 %.
               11 tests in `tests/test_benchmark_watch.py` (pure maths + on-disk
               reconstruction + a `run_suite` label-parity guard, since the whole
               design rests on reading exactly what the harness writes).
               812/812 pytest green (716 s), tsc clean, vite build clean
               2026-10-25 (no frontend change this task). **→ NEXT: M7-T2c.**
Session notes: M7-T2b landed cancellation latency + worker-crash recovery
               (§4.2 rows; §4.3 binding structural guarantees). Files:
               `benchmark/cancel_recovery.py` — pure
               `evaluate_cancellation_bounded` / `evaluate_recovery_consistent`
               verdicts (bounded iff CANCELLED + steps_completed < 6; consistent
               iff live FAILED+WORKER_CRASHED + store mirror agrees + 0 orphan
               run row + fresh-manager recover returns 0) plus real-worker
               `measure_cancellation_latency` / `measure_worker_crash_recovery`
               drivers on `JobManager.start_profile_job`. New CLI subcommand
               `benchmark guarantees` writes a self-describing JSON; exit 0
               iff both verdicts pass. 13-test suite
               (`tests/test_benchmark_cancel_recovery.py`): 11 pure + 2
               integration (5k-row fixture → RUNNING window wide enough
               for the cancel/kill to land while `profile_dataset` is
               still executing). GOTCHA: use `proc.kill()` (SIGKILL),
               NOT `proc.terminate()` — the spec's §6.3 crash-detection
               path requires the worker never emits a terminal event,
               which `terminate()` (graceful) would violate. GOTCHA: the
               store mirror uses column `state`, not `status`, on the
               `jobs` row — `store.get_job(job_id)["state"]`. GOTCHA:
               integration test fixture must be ≥ ~5k rows on macOS
               dev — smaller fixtures complete in <2s after spawn and
               the measurement returns the honest inconclusive verdict
               `not_cancelled`/`not_killed` (never a false pass). Baseline
               recorded in TESTING §4.3.2: bounded / 5.48s cancel latency,
               consistent / 0.055s crash detection on 2026-10-25 macOS
               13.7.8 x86_64. 801/801 pytest green, tsc clean, vite build
               clean. **→ NEXT: M7-T2a (10M–100M RSS ladder, scheduled run).**
Session notes: M7-T1 landed the benchmark subsystem. Files: `benchmark/generator.py`
               (`DatasetSpec(rows,columns,cardinality,missingness,distribution,seed)`;
               one DuckDB `COPY (SELECT … FROM range(n))` emits the whole fixture —
               NO Python row loop, honors ADR-007 at 100M; values are deterministic
               via a masked salted `hash` (thread/parallel-safe), seed decorrelates
               the salt space; core kinds pk_id/amount/stage(DuckDB list-literal)/
               category/event_date(DATE) + alternating extras; `write_dataset_config`
               emits a schema-valid §9 config referencing real columns). GOTCHA:
               DuckDB list literal is `['a','b'][int]`, NOT `list_value[...]`.
               `benchmark/harness.py` (`measure_case` spawns a FRESH child
               `python -m …benchmark measure` per run so `resource` peak RSS is that
               run's high-water, not the tree max — the ONLY honest way to compare
               RSS across sizes; pure `evaluate_rss_boundedness`/`evaluate_sampled_cheaper`
               gate the §4.3 binding structural guarantees; `run_suite` writes a
               self-describing results JSON + machine context). GOTCHA: `ProfilingRun.status`
               is a plain str under `use_enum_values` — `str(...)`, never `.value`;
               macOS `os.sysconf` lacks SC_PHYS_PAGES → RAM via `sysctl hw.memsize`;
               `resource` is absent on Windows → RSS None there. Baseline (macOS dev,
               high-card/skewed CSV): RSS 94→169MB over 200k→1M (×5 rows → **bounded**,
               no full materialization), sampled 144.7s < deep-exact 157.5s (**degrade
               cheaper**, marginal because quality rules run full-relation + query-count
               dominate — recorded honestly in §4.3.1). The 10M–100M ladder + cancel/
               recovery + packaged both-OS baseline are M7-T2/§5.1. **→ NEXT: M7-T2.**
Session notes: M6-T6 landed Report Findings & Recommendations (§3.4/§4.2–4.3) +
               Export (§3.5) — M6 COMPLETE. **ADR-020 / CONTRACTS §5.2 contract
               change**: added `recommendations.get` (8th read cmd; seven→eight) —
               the §4.3 recommendation *bodies* (action/rationale/do_not/reason_type)
               were unreachable (a Finding carries only `recommendation_ids`;
               `report.get_summary` is counts-only; the full bodies lived only in the
               `profile_json` artifact behind `artifact.open`, no bytes cross) — the
               identical column-body gap ADR-019 closed. Store already persisted the
               rows (`_insert_recommendations`/`load_recommendations`). Reused the
               ADR-019 pattern verbatim: `RecommendationsGetPayload(PagedResultPayload)`
               +`finding_id`, `_recommendations_get_handler` rebuilds each row through
               `MetadataStore.load_recommendations` (contract-shaped, §7 separation
               intact), pages under the §5.4 1 MiB cap. `list_recommendations` gained
               DEFAULTED `finding_id`/`limit`/`offset` kwargs so the existing
               full-list `load_recommendations` caller is unaffected; added
               `count_recommendations` mirroring `count_findings`. NO schema/model
               change (Finding/Recommendation already in pydantic↔TS parity).
               Frontend `pages/Report.tsx`: Findings cards (`findings.get`, severity/
               category filters, fingerprint, facts-as-evidence with MethodTag,
               interpretation + hypotheses rendered apart, never folded into a fact)
               each nesting its cited recommendation bodies resolved from a run-wide
               `recommendations.get` id-map; Export section = `artifact.open`
               (reveal) for the three kinds present in `summary.result_refs`.
               `styles.css` +71 (finding-card, sev/cat/reason pills, rec-block,
               export-row). GOTCHA: a recommendation `reason_type` ∈
               {evidence,heuristic,business_context_required} — a
               `business_context_required` one must render as an investigation
               question, never a proven cause (§4.3/§7). 775/775 pytest, tsc clean,
               vite build clean 2026-10-25. **→ NEXT: M7-T1.**
Session notes: M6-T5 landed Report Visualizations (§3.3) + Quality Assessment
               (§3.4). Files: `frontend/src/charts/plotly.ts` (spec builders
               mirroring html_report.py `_charts_for_column`/`_box_spec`/
               `_missingness_chart`, + lazy `loadPlotly()` of the vendored
               bundle), `charts/PlotlyChart.tsx` (newPlot/react + purge on
               unmount, placeholder-not-throw while Plotly loads so a chart never
               blocks the report §6.4), `vite-env.d.ts` (window.Plotly ambient;
               vite/client NOT wired since include=["src"]), `pages/Report.tsx`
               (+§3.3 missing-rate section, +§3.4 paged quality table with
               status/dimension selects, MethodTag for non-exact, ChartFigure),
               `styles.css` +31 (.plotly-*, .chart-figure, .q-status pills).
               Charts read ONLY the `report.get_columns` bodies already loaded —
               NO recompute, NO new command (ENGINE §10). No Python/contract
               change. 772/772 pytest, tsc clean, vite build clean, dist/
               plotly.min.js emitted 2026-10-25. **→ NEXT: M6-T6.**
Session notes: M6-T4 landed the Report View (Overview §3.1 + Column Profiling §3.2).
               **ADR-019 / CONTRACTS §5.2 contract change** — added the
               `report.get_columns` read command (7th read cmd; six→seven). Gap
               that forced it: NO prior §5.2 command returned the §3.2
               ColumnProfile metric *bodies* — `report.get_summary` is counts-only,
               `dataset.inspect` is identity+types, and the store's
               `column_metric_values` rows carry ONLY the scalar leaves
               (`missing_rate`/`distinct_count`/`stats.*`/`skewness`, via
               `_iter_column_metrics`) — histogram/categorical/temporal/null-count
               bodies live ONLY in the `profile_json` artifact. GOTCHA: the new
               handler reads that ARTIFACT (authoritative full doc,
               `result_document(bundle)["profile"]["columns"]`) via
               `store.artifact_ref(run,"profile_json")` + `ArtifactStore.read_text`,
               NOT the lossy store rows; pages ColumnProfile by ordinal, revalidates
               each through the pydantic `ColumnProfile` (parity — TS types were
               already in sync, NO schema/model change), response re-checked vs the
               §5.4 1 MiB cap. Addressed by run_id OR dataset→latest run
               (`_resolve_run_id`), so the UI `StartedJob` store deliberately does
               NOT carry run_id — `report.get_summary` supplies it. Files:
               `bridge/envelope.py` (+`ReportGetColumnsPayload(PagedResultPayload)`),
               `bridge/facade.py` (+`_report_columns_handler`, COMMANDS, docstring),
               `docs/CONTRACTS.md` §5.2 + `ARCHITECTURE_AND_ADRS.md` ADR-019, tests
               `test_bridge_read_commands.py` (+7, exact-set +1). Frontend:
               `pages/Report.tsx` (new — MetricCell renders ADR-015 ≈/sampled/n-c
               badges + per-column detail; NO fabricated column status — findings are
               M6-T6), `App.tsx` (/report→Report), `types/contracts.ts`
               (+ReportSummaryResult/ReportGetColumns*), `styles.css` +79. 772/772
               pytest, tsc clean, vite build clean 2026-10-25. **→ NEXT: M6-T5.**
Session notes: M6-T3 landed the Run Progress screen (CONTRACTS §5.5, §6).
               Files: `frontend/src/pages/RunProgress.tsx` (poll
               `profile.get_status` every 700ms — authoritative; optional
               `onPushedJobStatus` hints merged through the SAME apply() gate;
               state-rank ref (QUEUED<RUNNING<CANCEL_REQUESTED<terminal) stops
               a late/older transport message from regressing the bar, the ONE
               legal backwards case being a polled RUNNING after
               CANCEL_REQUESTED per §6.5; progress bar + completed/total_steps +
               current_step; Cancel -> `profile.cancel`, button locks on
               optimistic CANCEL_REQUESTED until a polled terminal confirms;
               JOB_NOT_FOUND stops the poll with an explicit message — §8 says
               not-retryable; SUCCEEDED renders the bounded `_bounded_result`
               summary — the poll's `result` field sits DIRECTLY on the JobStatus
               object, bridgeSend<R=JobStatus> returns it unwrapped, no extra
               envelope), `App.tsx` (+/progress route, nav "Progress"),
               `RunConfig.tsx` (accepted-job banner now links to /progress),
               `styles.css` (+62 lines progress bar/state badges).
               No Python/contract change; 765/765 pytest green, tsc clean,
               vite build clean 2026-10-25.
Session notes: M6-T2 landed the Run Configuration screen (CONTRACTS §9).
               Files: `frontend/src/pages/RunConfig.tsx` (mode radios;
               sampling toggle + strategy/size/seed — seed REQUIRED when
               enabled, ADR-015; language default(en)/en/id; rule editor with
               deterministic derived ids + conditional_not_null condition row;
               YAML/JSON §9 upload; live validation-issue panel; Start →
               `bridgeStartJob("profile.start")`, mode always sent as the
               `profile_mode` override), `frontend/src/config/validation.ts`
               (client-side MIRROR of dataset_config.schema.json + facade
               `_validate_config` hints — `parseConfigDocument` (js-yaml),
               `validateDatasetConfig`, `buildConfigFromForm`,
               `BRIDGE_MESSAGE_CAP_BYTES` pre-check; a UX hint only — the
               server-side schema+model check with CONFIG_INVALID stays the
               sole authority, AGENTS §5), `frontend/src/state/selection.ts`
               (cross-screen external stores via `useSyncExternalStore`:
               `SelectedDataset` set by the Dataset screen on inspect, and
               `StartedJob` for M6-T3 — in-memory only, the SQLite store stays
               the restart authority §6.3), `App.tsx` (route /run → RunConfig),
               `DatasetSelection.tsx` (+setSelectedDataset), `styles.css`
               (+99 lines). New npm dep: `js-yaml` + `@types/js-yaml`.
               Verified: §9 YAML exemplar → 0 errors on BOTH the client mirror
               and the server schema; form-built doc schema+model-valid;
               `language` omitted when unset (config_hash stability, ADR-018).
               GOTCHA found: `language` must be in the mirror's TOP_KEYS or the
               exemplar self-rejects (fixed same session). Upload reads file
               TEXT in the WebView and only the parsed document crosses the
               bridge — no new facade method and no path allow-list exposure
               needed (SECURITY §3); the dataset path stays a text input
               pending the pywebview Browse spike below. 765/765 pytest green,
               tsc clean, vite build clean 2026-10-25.
Session notes: M6-T1 landed the first real UI screen (Dataset Selection).
               Files: `frontend/src/bridge/api.ts` (typed §5.3 bridge client:
               `BridgeRequest`/`BridgeResponse`/`BridgeError`/`bridgeSend`/
               `bridgeStartJob`/`onPushedJobStatus`/`bridgeAvailable`),
               `frontend/src/pages/DatasetSelection.tsx` (path input, inspect
               panel with metadata grid + columns table + "last_run" badge,
               paged preview with PII mask toggle + lock icons + row counter),
               `frontend/src/App.tsx` (route `/` → DatasetSelection, nav label
               "Dataset"), `frontend/src/styles.css` (+~150 lines UI styles).
               No new Python/contract code; existing 765 tests unchanged.
               tsc clean, vite build 5.6s, frontend/dist regenerated.
               GOTCHA: pywebview v6 injects the js_api's public methods via
               `util.inject_pywebview` → `finish.js` → `window.pywebview._createApi`;
               the `create_file_dialog` on the Python Window object is NOT
               automatically available in JS. To add a "Browse" button the host
               must either expose a `select_file` method on the facade or call
               `create_file_dialog` from the Python side and push the path via
               `evaluate_js`. Kept as text input for now; revisit in M6-T2.
               765/765 pytest green, tsc clean, vite build clean 2026-10-25.
Session notes: M5-T3 landed the §5.5 progress push (CONTRACTS §5.5, ADR-005/006).
               Files: `bridge/worker/manager.py` (+`canonical_status_payload`,
               `add_status_listener`, `_notify`, `_dispatch_loop`, `_commit` =
               `_persist` + `_notify` on all 7 transition sites), `bridge/runtime.py`
               (+`configure_status_push` / `status_push_enabled` / `_forward_status`
               / `window_push_sink` / `JS_STATUS_CALLBACK="__dpeJobStatus"`,
               manager attaches the forwarder), `bridge/facade.py` (`_status_payload`
               now returns the canonical payload and KEEPS `job_id`),
               `bridge/envelope.py` (owns `PAYLOAD_CAP_BYTES`), `main.py` (installs
               the window sink), `frontend/src/types/contracts.ts` (+`JobStateValues`
               /`JobProgress`/`JobStatus`), `tests/test_contracts_ts_parity.py`
               (`job_status.schema.json → JobStatus`, enum set now 19),
               `docs/CONTRACTS.md` §5.5 (binding push details). GOTCHA: the pushed
               and polled objects are the SAME dict by construction — never fork a
               per-transport shape. GOTCHA: a push is dropped (>1 MiB, unserialisable,
               dead WebView) or coalesced (newest per job), so a UI must never treat
               a missing push as a lost state; it polls. 765/765 pytest green, tsc
               clean 2026-10-25.
Session notes: M5-T1 landed the read side of the bridge (CONTRACTS §5.2/§5.4,
               SECURITY §3/§4). Files: `bridge/envelope.py` (+7 payload models
               `DatasetInspectPayload` / `DatasetPreviewPayload` / `PagedResult
               Payload` / `QualityGetResultsPayload` / `FindingsGetPayload` /
               `ReportSummaryPayload` / `ArtifactOpenPayload`, `PREVIEW_ROW_CAP
               =200`, `SOURCE_PATH_MAX_LENGTH`), `bridge/facade.py` (+6 handlers,
               COMMANDS now the full 9-row §5.2 table, response-side 1 MiB check
               in `bridge_send`, `_resolve_source_path` allow-list, `_reveal_path`
               native viewer), `storage/metadata_store.py` (+`count_checks` /
               `count_findings` for `page.total`), `discovery/inference.py`
               (+public `PII_PATTERNS` so preview masks by exactly discovery's
               email/phone shapes), `bridge/errors.py` (+`DATASET_READ_FAILED`
               alias), `frontend/src/types/contracts.ts` (mirrored the new
               payloads + result shapes, `PreviewColumn.masked`),
               +`tests/test_bridge_read_commands.py` (38: allow-list equals the
               §5.2 table, inspect joins stored types, preview paging/row-cap/
               `_row_id` hidden/PII masking + `mask_pii=False`, paged contract
               objects validated against the JSON Schemas, filters, summary
               counts+refs only, artifact ack never content, traversal refused,
               reveal opt-in, oversized response, crash leak-free, retryable
               follows §8). GOTCHA: §8 is frozen and has NO run-address code, so
               an unresolvable `run_id`/`dataset_id` is `BRIDGE_REQUEST_INVALID`
               (mirrors `JOB_NOT_FOUND` covering an unknown job) — do not invent
               `RUN_NOT_FOUND` silently. GOTCHA: `artifact.open` returns an ACK
               (`run_id`/`kind`/`ref`/`size_bytes`) only — a ref is honoured only
               when a `run_artifacts` row records it, never from caller text.
               753/753 pytest green, tsc clean 2026-10-25.
Session notes: M4-T4 landed the report/narrative language decision
               (ADR-018; BLUEPRINT Q1 ACCEPTED). Files:
               `recommendations/templates.py` (bilingual catalog:
               `_RULE/_PROFILE/_GENERIC_TEMPLATES_ID` added, `_build_templates(
               tpls)` now per-language, `TEMPLATES_BY_LANGUAGE`, `TEMPLATES` kept
               as the EN view for back-compat, `templates_for_language(lang)`
               hard-errors on unsupported, `DEFAULT_LANGUAGE="en"`,
               `SUPPORTED_LANGUAGES`), `recommendations/narrative.py`
               (`subject_column(finding, language)` — only the dataset-level
               fallback label is localized: en "the dataset" / id "dataset ini"),
               `recommendations/rule_based.py` + `recommendations/__init__.py`
               (`language` param threaded, default en), `orchestration/__init__.py`
               (`build_recommendations(..., language=config.language)`),
               `schemas/models.py` (`ReportLanguage` enum + `DatasetConfig.
               language: Optional[ReportLanguage]=None`), `dataset_config.schema.json`
               (optional `language` enum [en,id]), `frontend/src/types/contracts.ts`
               (`ReportLanguageValues` + `language?:`), `tests/test_contracts_ts_parity.py`
               (enum set 17→18), `tests/test_recommendations.py` (+12
               `TestNarrativeLanguage`), docs BLUEPRINT Q1 / CONTRACTS §9 /
               ARCHITECTURE ADR-018. GOTCHA: `DatasetConfig.language` MUST be
               Optional-with-None (not default "en") so an unset field is dropped
               by `ContractModel._dump` — a non-None default changes `config_hash`
               and breaks the golden byte-diff + §9 round-trip tests. GOTCHA:
               adding the `ReportLanguage` TS enum bumps the hard-coded
               `test_all_17_contract_enums_are_mirrored` set → rename/extend to 18.
               GOTCHA: `templates_for_language` accepts str / enum / None; the
               worker sees `config.language` as a plain str under use_enum_values
               (or None). Unsupported language raises, never silently falls back.
               706/706 pytest green; tsc clean; golden unchanged (en default).
Session notes: M4-T3 landed the CSV issue export (ENGINE §10, SECURITY §4).
               Files: `reporting/csv_export.py` (render_issue_csv /
               write_issue_csv / emit_issue_csv_artifact; ISSUE_CSV_COLUMNS
               pinned verbatim to §10), `tests/test_reporting_csv.py` (19),
               `cli/__init__.py` (+`--csv OUT` on `profile`),
               `reporting/__init__.py` docstring updated. GOTCHA: a rule
               finding's originating rule is recoverable ONLY from its
               `rule:<rule_id>` evidence ref — the rule-derived *fact* ref is
               `metric:<rule_id>` and a profile-extreme ref is
               `metric:<column>.<measure>`; match the `rule:` prefix, not the
               `metric:` one, when joining finding→check. GOTCHA: profile-
               extreme findings (near-total missingness, dominant category,
               duplicate keys) carry NO `rule:` ref and are NOT tracker issues
               by design, so the issue CSV has fewer rows than the findings
               list. GOTCHA: SECURITY §4 masking is a no-op here by
               construction — an issue row never carries a raw source value,
               only ids/names/aggregates/config-thresholds — do not add a
               mask_pii flag that would be dead code. GOTCHA: emit under
               kind=`issue_csv` (RESULT retention, already registered in
               DEFAULT_FILENAMES + KIND_RETENTION) — no new kind needed.
               694/694 pytest green.
Session notes: M4-T2 landed the self-contained HTML report (ENGINE §10,
               SECURITY §4). Files: `reporting/html_report.py`
               (render_html_report / write_html_report /
               emit_html_report_artifact; consumes
               `result_document(bundle)` — the exact M4-T1-pinned shape, zero
               recomputation; sections: status counts, run metadata +
               error_log + sampling record, column cards with every MetricValue
               wearing its method badge (tooltip carries seed/algorithm/
               reason), quality table (rule anchors `#rule-<id>`), findings
               with fingerprint + evidence back-links, recommendations),
               vendored `reporting/assets/plotly.min.js` (from the plotly
               7.1.0 wheel's package_data; sha256 3b6e15d4…68d721f pinned in
               `tests/test_reporting_html.py`; NOT a runtime dependency —
               requirements.txt unchanged per ADR-012 minimalism),
               +`tests/test_reporting_html.py` (25 tests: asset pin, no
               `<script src>`/`<link>` tags, golden-fixture e2e render,
               byte-determinism, badge/seed tooltips, not_computed→reason,
               email/phone masking default + `mask_pii=False` override,
               escaping, chart kinds incl. §10 outlier box, ArtifactStore ref
               `artifacts/<run>/report.html`, CLI `--html` flag),
               `cli/__init__.py` (+`--html OUT` on `profile`, JSON path
               untouched), `data_profiling_engine.spec` (assets dir added to
               `datas` — PyInstaller doesn't auto-collect package data files),
               `reporting/__init__.py` docstring now defers only CSV to M4-T3.
               GOTCHA: emitted under kind=`html_report` (RESULT retention per
               the M3-T2 KIND_RETENTION registry), NOT the "EXPORT" wording of
               the old NEXT memo — RESULT already carries the survive-upgrade/
               explicit-delete lifecycle, and `html_report` is the canonical
               kind in DEFAULT_FILENAMES + CONTRACTS §2 result_refs; do not
               "fix" this to EXPORT. GOTCHA: histogram `bins` are one LEFT
               EDGE per bin (len == counts), not nb+1 edges — chart centers
               derive step from edges[1]-edges[0]. GOTCHA: detector refs are
               `rule:<rule_id>` and `metric:<column>.<measure>`
               (detectors._metric_ref), not `measure:column` — the anchor
               resolver matches the column set. GOTCHA: a report file is
               ~4.8 MiB (inline Plotly); browser automation must poll
               readyState, not rely on the navigation ack. GOTCHA: the
               temporary `uv pip install --no-deps plotly` extraction helper
               is still in `.venv` (uninstall commands hung on the broken pty
               host this session) — harmless (not in requirements, excluded
               by the spec build); remove it whenever convenient. Browser-
               verified: 7 charts render offline, 0 console errors, box quartiles
               match the card stats 2026-10-24. 675/675 pytest green, tsc clean.
Session notes: M4-T1 landed the golden-snapshot harness the M2-T11 writer
               defers to (ENGINE §10 / TESTING §2). Files:
               `tests/golden_harness.py` (normalize_document / dump_stable /
               emit_result_document / load_config / golden_path /
               read_or_update_golden; VOLATILE registry enumerates ONLY
               run.dataset.source_ref, source_snapshot.{path,modified_at},
               engine.{duckdb_version,python_version},
               timing.{started_at,finished_at,duration_ms→REDACTED_INT=0},
               result_refs and per-check `executed_at`; reproducibility
               tuple content_hash+config_hash+engine_version and every
               metric/fingerprint/status/provenance.method stays verbatim),
               `tests/fixtures/golden/sales_deals.{csv,config.json,golden.json}`
               (40-row deterministic fixture, 12 duplicate deal_ids +
               every-7th missing deal_value + constant is_new + 3-stage
               cycle; CSV sha256 9b9cf013…c2fa3, size 1606 B),
               `tests/test_reporting_golden.py` (8 tests: byte-diff on
               normalized canonical JSON with compact first-mismatch
               context; every block re-validated against its JSON Schema
               FROM the checked-in golden, not from a live emit; volatile
               ARE redacted / reproducibility tuple IS NOT; two-emit
               stability; the injected FAILs (`deal_id_unique`,
               `value_not_null`) + `stage_domain` PASS + fingerprints +
               recommendation back-refs are present; config_hash ↔ golden
               coupling; fixture-triple presence; harness deep-copy
               self-check), and root `.gitattributes` freezing
               `data_profiling_engine/tests/fixtures/golden/*` with `-text`
               so a Windows autocrlf checkout cannot shift `content_hash`
               (the golden's whole point). Regeneration: `DPE_UPDATE_GOLDEN=1
               .venv/bin/python -m pytest ...test_reporting_golden.py -k
               matches_golden` (or invoke
               `tests.golden_harness.emit_result_document + dump_stable +
               Path.write_text` directly, as this session did). Deliberately
               OUT: no HTML/CSV writer changes (M4-T2/T3), no `executed_at`
               parameter added to `profile_dataset` (would have been a
               cross-module change; the harness redacts it instead), no
               1M-row scale harness (BLUEPRINT Phase 1 gate is measured in
               M7 benchmarks, not here). GOTCHA: `run.timing.duration_ms`
               is `integer/minimum:0` in `profiling_run.schema.json`, so a
               string placeholder would break schema validation — use
               `REDACTED_INT=0` for any integer-typed volatile slot. GOTCHA:
               `quality/executor.py::_now_iso` stamps `executed_at` per
               check when `run_dataset_profiling` doesn't pass one through
               (`profile_dataset` doesn't forward it either) — that field
               is wall-clock and now redacted; if a future task adds an
               `executed_at` passthrough on `profile_dataset`, drop the
               per-check redaction so the golden regains coverage.
               650/650 pytest green, tsc clean 2026-10-24.
Session notes: M3-T3 landed the normalized metric-value half of ADR-008. New
               forward migration v2 (`MIGRATIONS` now ends
               `Migration(2,"m3t3_column_metric_values")`; `TARGET_VERSION`
               auto=2; v1 DDL UNTOUCHED/frozen) creates `column_metric_values`
               keyed `(run_id, column_name, metric_name)` — one row per §3.2
               `MetricValue` leaf, carrying ADR-015 `method`+`method_params`
               (sampled seed lives inside method_params)+`reason`+`denominator`
               and a `value_json` that keeps absent-vs-null (§1) via the same
               `_field_json` the checks path uses. FK→runs ON DELETE CASCADE; two
               indexes `(run_id,metric_name)` + `(column_name,metric_name)` are
               the M6 diff access paths. `_iter_column_metrics(ColumnProfile)`
               (metadata_store.py) ENUMERATES the promoted leaves explicitly
               (missingness.missing_rate, uniqueness.distinct_count,
               stats.{min,max,mean,median,p25,p75,p95,p99,stddev},
               distribution.skewness) — a generic walk was rejected so a
               provenance-bearing non-scalar (histogram/outlier block) can never
               leak into a diff cell. `save_run` now calls
               `_insert_column_metrics` in the SAME one transaction. Reads:
               `column_metrics(run_id, column_name=, metric_name=)`,
               `metric_value(run_id, column, metric)→Optional[MetricValue]` (the
               single-cell read that avoids parsing the artifact; `_row_to_metric_value`
               reuses the checks reconstruction), and `metric_history(dataset_id,
               column, metric, limit=)` joining runs newest-first for M6. The
               artifact FILE stays authoritative for anything not promoted
               (histogram bins, category arrays, type split) — these rows are a
               projection/index, never a second source of truth. `counts()` now
               includes `column_metric_values`. NO new metric; no contract shape
               change; no UI; no HTTP (ADR-005). Migrated test suite: v1-only
               assertions bumped to v2 (fresh-db target/`applied`/stamps,
               appended-migration & atomic-failure fixtures shifted to version
               3, pre-migration backup now `v2`), `counts` dict + tables set
               updated. +`tests/test_storage_metadata.py` (55→64: promotion set,
               single-cell read, absent→None (not 0), sampled seed + denominator
               survive, not_computed value stays ABSENT (not null) + reason,
               every promoted cell == its artifact source, metric_history
               newest-first + limit, FK cascade, and a REAL `profile_dataset` run
               round-tripped cell-by-cell). 642/642 pytest green, tsc clean
               2026-10-24.
Session notes: M3-T2 landed the FILE half of ADR-008 in a new
               `storage/artifact_store.py` (was an empty stub; joined via
               `storage/__init__.py`). Layout: `<data_root>/artifacts/<run_id>/
               <file>`, `ref_path` STORE-RELATIVE (`artifacts/<run_id>/<file>`,
               `STORE_PREFIX`=paths.ARTIFACT_SUBDIR so the two layers cannot
               drift) — matches CONTRACTS §2 `result_refs` example
               `artifacts/DP-0001/profile.json` and the v1 migration comment;
               `run_artifacts` rows unchanged. `ArtifactStore(data_root=,
               staging_root=, cache_root=)` resolves via paths (reuses
               `resolve_data_root`/`artifacts_dir`/`ensure_private_dir` 0o700 +
               app-bundle refusal — NOT re-implemented). API: `ref_path/
               resolve/write_text|write_bytes|write_document(deterministic
               sort_keys JSON)/read_*/exists/open_path` (the `artifact.open`
               entry). Safety: `resolve()` confines every ref inside the store
               (realpath check defeats `..`/abs/symlink escape) → SOURCE_NOT_ALLOWED;
               bare-filename validation on run_id+filename; all IO wrapped in
               `ArtifactStoreError(code=ARTIFACT_WRITE_FAILED)` so no raw
               OSError reaches the bridge. Lifecycle: `RetentionPolicy`
               {RESULT,EXPORT,STAGING,REBUILDABLE} + `KIND_RETENTION`/`retention_for`
               (unknown→RESULT, conservative); run store holds only RESULT/EXPORT
               (survive upgrade, explicit-delete); staging lives in temp_root via
               `stage()` contextmanager that rmtree's on exit (crash-safe,
               SECURITY §4); cache is a separate rebuildable root. Cleanup:
               `delete_run_artifacts(rows)` (explicit; idempotent, prunes empty
               run dir, skips escaping refs), `delete_dataset_artifacts(store,
               dataset_id)` (§4 "delete this dataset's history and artifacts"),
               `sweep_orphans(store)` (deletes files under `<run_id>/` dirs with
               NO `run_artifacts` row — the ADR-006/§6.1 crashed-run/partial-
               artifact net; never touches a referenced file), `clear_staging/`
               `clear_cache`. New read-only join helper `MetadataStore.
               all_artifact_refs()` (no restructure of ref_path) is the sweep's
               authority for live files. Deliberately OUT: rows are still the
               metadata store's to delete (M5 wires the user action to remove
               BOTH rows and files); no export writes (M4). +`tests/
               test_storage_artifact_store.py` (26: layout/filenames, store-
               relative+traversal+abs confinement, 0o700 private dir, determinism,
               staging cleanup, retention map, delete idempotent+skip-escape,
               clear_staging/cache, and 4 metadata-join tests over a REAL
               `profile_dataset` run: rows→files resolve, orphan sweep keeps the
               row-referenced file, dataset delete, bundle `result_refs` path).
               633/633 pytest green, tsc clean 2026-10-24.
Session notes: M3-T1 landed the metadata half of ADR-008 in a new
               `storage/` package (was an empty stub). `storage/paths.py` =
               SECURITY §2 directory resolution — `default_data_root/`
               `cache_root/temp_root` from OS locations (`~/Library/Application
               Support`, `%APPDATA%`, `XDG_DATA_HOME`) with `DPE_DATA_DIR`/
               `DPE_CACHE_DIR` overrides for packaging/tests, plus
               `ensure_outside_app_bundle()` (refuses a user-data path inside
               `sys._MEIPASS`/frozen-exe dir → an upgrade can never destroy it,
               §6.5) and `ensure_private_dir()` (0o700 on POSIX);
               `metadata_db_path/backups_dir/artifacts_dir` hang off the root.
               `storage/migrations.py` = the ADR-008 "migration mechanism":
               gapless forward-only `Migration(version,name,sql)` chain
               (`MIGRATIONS=(Migration(1,"m3t1_initial_metadata",…),)`,
               `TARGET_VERSION=1`), bookkeeping table `schema_migrations` +
               mirrored `PRAGMA user_version`, refuses a DOWNGRADE ("restore a
               pre-migration backup") and refuses to touch an UNVERSIONED db
               that already has tables, writes `backup_database()` copy
               `metadata.pre-migration-v{n}-{ts}Z.sqlite3` into
               `<db dir>/backups` before applying anything (skipped when
               `before==0` — nothing to preserve), and applies each migration in
               ONE manual `BEGIN IMMEDIATE` … `COMMIT` with ROLLBACK on failure
               (verified: a failing DDL leaves the version unchanged and creates
               no partial tables). v1 DDL = 19 tables: datasets,
               dataset_config_versions, jobs, runs, dataset_profiles, run_columns,
               quality_checks, check_evidence_refs, findings + 5 finding children
               (evidence_refs/facts/hypotheses/related_refs/recommendation_refs),
               recommendations + 2 children, run_errors, run_warnings,
               run_artifacts. `storage/metadata_store.py` = typed writer/reader;
               `save_run(bundle, config=…, artifact_refs=…)` persists a whole
               M2-T11 `ProfileBundle` in ONE transaction (§2 run, §3.1 profile,
               column inventory, §4.1 checks + ERROR-only error columns, §4.2
               findings with indexed-not-unique ADR-016 fingerprint, §4.3
               recommendations, error_log/warnings, artifact refs) and every
               block reads back schema-valid + equal to what went in
               (`load_profiling_run/load_check_results/load_findings/
               load_recommendations`); registry/config-version/job APIs
               (`register_dataset`, `save_config_version`, `create_job`,
               `save_job_status`, `unfinished_jobs`) are there for M5; history/
               diff queries `list_runs/latest_run/runs_by_fingerprint/
               check_status_counts/counts` back M6's run-history screen.
               Connection setup `journal_mode=WAL` + `foreign_keys=ON` +
               `busy_timeout=5000` + `isolation_level=None` = ADR-008
               "concurrent read during run" (test has a writer thread + a
               second reader store on the same file) and "worker crash does not
               corrupt the store" (per-run atomicity). NEW §8 code
               `METADATA_WRITE_FAILED` (retryable, source=`storage`) wrapped in
               `MetadataStoreError`/`MetadataIntegrityError` so sqlite internals
               never leak to the bridge. Deliberately OUT of scope (by milestone
               order): the artifact FILE store + lifecycle (M3-T2) and normalized
               `(run_id,column,metric)` rows (M3-T3) — result documents stay
               referenced files; `runs`/`run_columns` carry only scalars +
               identity/type columns. No contract shape changed, no new metric,
               no server (ADR-005). +`tests/test_storage_metadata.py` (55:
               directory policy, migration chain/backup/atomicity/downgrade&
               unversioned refusal, registry/jobs, synthetic per-block
               write→read-back equality incl. absent-vs-null + FAIL≠ERROR +
               sampled-seed survival, one REAL 60-row `profile_dataset` run
               persisted + WAL concurrency). 607/607 pytest green, tsc clean
               2026-10-24.
Session notes: M2-T11 closed the contract rollup M2-T10 deferred. New
               orchestration/rollup.py executes the dataset-scope plan
               decisions (schema.row_count / column_count / key_candidates —
               the ones no profiler touched) and folds the pipeline result +
               those scalars into the frozen §3.1 DatasetProfile (row_count
               MetricValue + column_count + primary_key_candidates +
               temporal_coverage from the first discovered temporal column +
               columns) and §2 ProfilingRun (dataset/source_snapshot from the
               handle, config{config_version,config_hash=sha256(model_dump_json),
               profile_mode}, engine{engine_version,duckdb,python}, timing,
               sampling=the M2-T10 RunSampling record, terminal status,
               error_log=result.errors). `profile_dataset()` is the single
               spine entry (also the M5 worker target). reporting/__init__.py
               = deterministic JSON writer (result_document + write_profile_json,
               sort_keys byte-stable). cli/__init__.py = argparse
               `profiler-cli profile --input --config --mode --out
               --dataset-id --run-id --allow-root`; --config loads the §9 YAML
               authoring form via `yaml.safe_load` (JSON is a YAML subset;
               BLUEPRINT Phase 1 `--config x.yaml`; ADR-012 — YAML is syntax,
               not a framework) and rejects an invalid config BEFORE compute
               (AGENTS §5). Honesty: row_count/column_count stay `exact` under
               sampling (recomputed on the full view, never relabelled);
               key_candidates verified over the FULL relation
               (requires_full_relation), absent (omitted) when not_computed —
               never fabricated; per-task errors ride error_log without
               demoting the run (§6.4 FAIL≠ERROR); sampled REQUIRES seed
               (ADR-015); not_computed carries reason + NO value. NO new
               metric; no HTTP/server (ADR-005); packaged app entry stays
               main.py (spec unchanged). Gotchas: ContractModel rejects an
               explicit null on a non-nullable optional field → build rollups
               via `_instantiate()` which OMITS None keys not in
               NULLABLE_FIELDS; added `pyyaml>=6.0` to requirements.txt (venv
               is uv-managed → `uv pip install … --python .venv/bin/python`, no
               pip binary); run_dataset_profiling on a 200-row/5-col real CSV in
               standard mode costs ~13s (test file shares ONE module-scoped
               bundle across read-only asserts to keep the 20-test file ~165s).
               +tests/test_cli_rollup.py (20). 551/551 pytest green, tsc clean
               2026-10-24.
Session notes: M2-T10 finalised the reservoir (profiler._sample_relation →
               DuckDB `USING SAMPLE RESERVOIR (n ROWS) REPEATABLE(seed)`, real
               bounded+seeded, was the md5 ORDER+LIMIT placeholder; `block` =
               bounded scan) AND wired the engine path end-to-end in
               orchestration/__init__.py (run_profiling_pipeline +
               run_dataset_profiling + open_source): config.mode/sampling/
               sections → discover → plan_execution → the 3 M2-T4/T5/T6 passes
               share ONE plan+reservoir → _merge_profiles composes the
               family-disjoint blocks onto each ColumnProfile (model_copy) →
               run_quality_checks → build_findings → build_recommendations,
               returning ProfilingPipelineResult (columns/checks/findings w/
               recommendation_ids wired/recommendations/errors union/RunSampling
               record/plan). A sampled run stamps method=sampled+seed on EVERY
               sampled metric (ADR-015), verified by walking plan decisions +
               emitted provenance. **Deliberately NOT done (→ M2-T11):** the
               §3.1 DatasetProfile / §2 ProfilingRun rollup — the runner never
               executes plan.dataset_decisions (schema.row_count/column_count/
               key_candidates have NO executor yet); M2-T11 folds the result +
               those dataset metrics into the full contracts + JSON writer.
               Gotcha: `_sample_relation`'s defensive DROP had to switch VIEW→
               TABLE (same window name reused across the 3 passes). No contract
               change, no new metric. +tests/test_orchestration_modes_sampling.py
               (19). 531/531 pytest green, tsc clean 2026-10-24.
Session notes: M2-T9 delivered the ACTING side in the recommendations/ package
               — templates.py (ENGINE §7 closed rule/template catalog: a
               RecommendationTemplate{kind,reason_type,action,rationale,
               target_improvement,do_not,probe} per problem-kind; 6 rule kinds
               keyed by finding.category + 4 profile-extreme kinds recovered
               from the representative fact metric_ref suffix — missingness /
               constant / duplicate-key / dominant-category + 6 defensive
               generics = 16 templates; classify_finding_kind(Finding)→kind
               because the frozen §4.2 Finding carries no detector_id, identity
               is re-derived: any `rule:` evidence ref ⇒ rule kind by category,
               else facts[0].metric_ref suffix ⇒ profile kind; a review signal
               — accuracy_flagged rule OR dominant-category profile — is
               reason_type=BUSINESS_CONTEXT_REQUIRED phrased as an
               investigation question, never a proven cause, per ENGINE §5
               "accuracy is never inferred from validity"), narrative.py
               (composes action/rationale/target/do_not by substituting ONLY the
               {column} slot — numbers stay in the cited evidence refs, never
               re-typed into prose; probe folded into the action sentence; the
               rationale quotes the evidence refs verbatim so every
               recommendation is traceable), rule_based.py
               (recommendation_for_finding: copies finding.evidence_refs
               VERBATIM onto the Recommendation; reason_type=evidence REQUIRES
               them (degrades to business_context_required if somehow empty);
               status=proposed, schema_version='1.0', recommendation_id R-NNN,
               finding_id back-ref), __init__.py (build_recommendations(findings)
               → RecommendationsResult{recommendations,findings}: one rec per
               finding (≥1 mandate met), R-NNN assigned in the already-prioritized
               F-NNN order → deterministic; and Wires Finding.recommendation_ids
               that M2-T8 left [] via a model_dump→set→model_validate round-trip
               so the strict ContractModel emits the key (fields_set) and the
               rewired finding re-validates §4.2). **No contract change** —
               reuses frozen Recommendation pydantic + recommendation.schema.json
               (§4.3 already frozen M1-T2) + the finding.schema.json
               recommendation_ids array; consumes ONLY the M2-T8 Finding[].
               +tests/test_recommendations.py (24: each detector→expected kind
               incl. all 4 profile extremes + rule-by-category + every kind
               resolves; evidence⇒non-empty refs & verbatim subset of the
               finding, rationale quotes refs, dominant-category/accuracy-rule
               ⇒business_context_required with an investigation-verb action;
               column substituted but the observed 0.42 value NOT reproduced in
               action; every rec §4.3-schema-valid (format on) + R-NNN +
               finding_id back-ref + status proposed + ≥1 rec per finding;
               recommendation_ids wiring before/after + wired finding still
               §4.2-valid + all other fields preserved; byte-stable rerun;
               completeness zero-fill do_not guardrail; TEMPLATES covers all 6
               QualityDimension rule kinds). AI/LLM stays OUT of MVP
               (BLUEPRINT Q3). 512/512 pytest green, tsc clean 2026-10-24.
Session notes: M2-T8 delivered the INTERPRETATION side in a new findings/
               package — fingerprinting.py (ADR-016 §4.4:
               compute_fingerprint = sha256(category|scope_key|detector_id|
               normalized_measure) joined with 0x1F/0x1E control chars so no two
               field tuples collide; build_scope_key = dataset_id+table?+column,
               table omitted for MVP file sources; identity rides rule_id/metric
               id, NOT the value → same problem different numbers = same digest),
               detectors.py (Candidate dataclass + two §6.1 sources:
               detect_from_quality_checks — FAIL/WARN → finding carrying the
               rule's observed Provenance, evidence_refs [metric:rule_id,
               rule:rule_id]; **PASS and ERROR both emit nothing** (an
               un-executable check is not a proven problem, FAIL≠ERROR);
               confidence mirrors method exact→high/approx→medium/sampled→low;
               detect_from_profiles — rule-less ColumnProfile extremes as WARN:
               near-total missingness (missing_rate≥NEAR_TOTAL_MISSINGNESS=0.90,
               affected=null+blank+sentinel), constant column (distinct==1 &
               denom≥2 → UNIQUENESS), dominant category (max/sum freq≥
               DOMINANT_CATEGORY_SHARE=0.95 → ACCURACY_FLAGGED, exact provenance
               but MEDIUM confidence for the top-K bound), duplicate-key
               auxiliary (ProfilerResult.duplicate_group_counts>0, the
               contract_target=None signal M2-T5 parked for here; EXACT
               provenance + reason). Facts/interpretation/hypotheses stay
               structurally separate (CONTRACTS §7); every hypothesis MVP
               status=unverified; no number is invented — each cites its metric
               ref), pipeline.py (dedup_and_merge collapses same (column,
               category) keeping the highest-priority representative's identity
               and unioning facts/evidence/hypotheses; priority_of = a
               deterministic int f(severity×1e5, critical-PK boost,
               affected-share band, confidence) — priority is ORDERING ONLY, the
               frozen §4.2 Finding has no priority field so NOTHING was added to
               the schema; assemble_findings sorts by (-priority, fingerprint) →
               finding_id F-001.., workflow=null, scope.column set, MVP
               recommendation_ids left [] for M2-T9 to wire). __init__.py exposes
               build_findings(dataset_id, run_id, config, profiles, checks,
               duplicate_group_counts?, row_count?) → FindingsResult (critical
               scope from config.dataset.primary_key per §6.2). **No contract
               change** — reuses the frozen Finding pydantic +
               finding.schema.json + finding_workflow; consumes only M2-T4..T7
               evidence. +tests/test_findings.py (29: fingerprint stability across
               a value change + column/rule/table distinction + pure-function
               determinism; FAIL/WARN→finding, PASS/ERROR→none; confidence↔method;
               each profile detector fires past its bound and is silent below
               (0.5 missingness, 7 distinct, 55/45 category, single-row
               constant); all profile findings severity=warning; dedup collapses
               rule+profile completeness on one column with merged evidence;
               priority puts critical/PK first + deterministic rerun; every
               finding §4.2-schema-valid (format on) + workflow null + F-NNN +
               hypotheses unverified + facts/evidence minItems 1; an integration
               run_quality_checks→build_findings on the sales fixture).
               488/488 pytest green, tsc clean 2026-10-24.
Session notes: M2-T7 delivered the QUALITY side (first non-ColumnProfile task)
               in a new quality/ package — registry.py (ENGINE §5.1 closed
               catalog: RuleDefinition{measure∈count|rate|outlier, param_kind,
               warn_only, mvp_active}; referential_integrity/date_order are
               mvp_active=False), compiler.py (compile_rule → ONE DuckDB
               statement shaped (violations,total,sample_row_ids,lower_bound,
               upper_bound); mirrors render_sql single-materialisation + ADR-017
               BOUND input — every threshold/domain/condition value is a $n
               placeholder, only identifiers quoted inline; rule target +
               condition column validated present else RuleCompileError→ERROR;
               ENGINE §5.2 missing predicate = null OR trim='' — sentinel set
               deliberately NOT folded into the rule's missing_rate_max (that is
               the ColumnProfile metric); unique violations = nonnull−distinct
               (per-group, NOT a window SUM(grp_n−1) that double-counts);
               outlier_scan reuses Tukey IQR/1.5 quantile_cont fences surfaced
               into threshold{between}; sample_row_ids via a bounded ORDER BY rid
               LIMIT≤200 array_agg subquery — row:N refs, never raw values; the
               unique/outlier CTE SELECT has NO outer FROM so an empty relation
               still returns one row), executor.py (run_quality_checks(con,
               handle, config, run_id, discovery?, executed_at?) →
               QualityRunResult{checks per rule in config order, errors §6.4};
               status PASS(0 viol)/FAIL(viol>0)/WARN(warn_only | business_note |
               severity==warning)/ERROR(cannot compile|execute → RunErrorEntry
               RULE_EXEC_FAILED + observed null, FAIL≠ERROR); non-error observed
               = MetricValue{count or rate, exact provenance, denominator=
               population}; schema_version reuses discovery SCHEMA_VERSION
               '1.0', rule_version='1' (the frozen §9 rule carries no version
               field). +tests/test_quality_rules.py (29 tests: registry parity +
               phase-gate; every §5.1 MVP type vs a hand verdict — not_null/
               unique/missing_rate_max/gte/lte/range/valid_domain/
               conditional_not_null (the §5.2 example)/type_valid (e2e PASS +
               direct INTEGER-cast FAIL); outlier WARN-not-FAIL with fences +
               no-outlier PASS; business_note + warning-severity → WARN; FAIL≠
               ERROR — absent column / Phase-2 / broken execution (quantile on
               VARCHAR) / malformed range all ERROR w/ observed null; §6.4
               one-broken-doesn't-abort; a bound 'it''s' domain value matches
               literally; row:N evidence refs + ≤200 cap on a 300-row fixture;
               every result §4.1-schema-valid w/ format checking). 459/459
               pytest green, tsc clean 2026-10-24.
Session notes: M2-T6 delivered the third-family pass in
               engine/metrics/profiler.py —
               execute_distribution_and_temporal(con, handle, report, plan,
               config, registry) consumes the M2-T3 ExecutionPlan and stamps
               ColumnProfile.distribution / .temporal as contract blocks
               (Distribution/Histogram/OutlierCandidates + Temporal/
               TemporalRange), reusing the M2-T4/T5 honesty + §6.4 per-task
               error policy + render_sql + family-skip discipline
               (DISTRIBUTION_TEMPORAL_FAMILIES).
               **Distribution**: histogram BUILT here via a width_bucket
               EMULATION (DuckDB 2.0 has no width_bucket scalar — STATE
               gotcha): edges span [min,max] over HISTOGRAM_BIN_COUNT=10 fixed
               bins, `CAST(LEAST(nb, FLOOR((col-lo)/(hi-lo)*nb)+1) AS INTEGER)`
               bucket ids, counts aligned to bins; top edge folds into the last
               bin; a constant column collapses to a single [lo] bin; an empty
               column is honest not_computed (bins/counts present but []); the
               bin_strategy/lower_edge/upper_edge ride in method_params
               (§2.2). skewness renders the templated `skewness(col)` (DuckDB
               returns adjusted Fisher–Pearson **G1**, NOT Polars' biased g1 —
               gotcha); undefined (<3 rows / zero variance) → not_computed +
               reason, value key ABSENT. outlier_candidates BUILT here via
               Tukey IQR/1.5 fences (`_build_numeric_summary_sql` reuses one
               quantile_cont q1/q3 pass): emits
               OutlierCandidates{count, method='iqr_1_5', provenance,
               sample_row_refs=[f'row:{id}']} — row *references*, never raw
               values (SECURITY §4); refs omitted when count==0.
               **Temporal**: range_min/max via render_sql (cast_varchar_on_fetch
               → CAST AS VARCHAR, TZ-safe, no pytz); volume_per_granularity
               BUILT here (date_trunc + auto granularity from the span via
               _pick_granularity — ≤_GRANULARITY_DAY_SPAN days→'day',
               ≤_GRANULARITY_MONTH_SPAN→'month', bounded VOLUME_MAX_BUCKETS);
               gaps = expected-minus-observed period series (generate_series
               INTERVAL, GAPS_MAX_PERIODS bound); out_of_expected_range honestly
               not_computed (no MVP dataset-config expected-range bound exists —
               never fabricate); timezone_source naive/utc derived from the
               physical type. Volume/gaps embed a Provenance inside an OPEN
               dict, so it MUST be `.model_dump(mode='json')` (a raw Provenance
               object is not JSON-serialisable — gotcha). Section gating: a
               disabled `distributions`/`temporal` section ⇒ the whole block is
               structurally OMITTED (dist_active/temporal_active flags), never
               zeroed. Approximate-without-approx-template (quick-mode
               histogram/skewness) falls back to exact SQL keeping the
               planner's approximate+algorithm label. +
               tests/test_profilers_distribution_temporal.py (28 tests:
               histogram bin-count + non-null sum invariant vs Polars
               drop_nulls, edges/strategy recorded, top-edge folds to last bin,
               constant single bin, empty not_computed; skewness == adjusted G1
               reference + explicit test it DIFFERS from Polars biased g1 +
               constant not_computed; outlier IQR count vs hand Tukey + refs
               are 'row:N' not values + no-outliers omit + seeded fixture
               count=2; temporal range varchar / month granularity volume /
               missing-month gap / day-granularity short-span gap /
               out_of_expected_range not_computed / naive-vs-utc timezone;
               section-gating distributions-off & temporal-off omit the block;
               quick approximate label+algorithm; sampled gaps seed+bounded+
               deterministic; §6.4 broken-skewness isolated from siblings;
               every profile §3.2 schema-valid across STANDARD/QUICK/DEEP +
               byte-stable rerun + numeric-has-no-temporal-and-vice-versa).
               430/430 pytest green, tsc clean 2026-10-24.
Session notes: M2-T5 delivered the uniqueness + categorical pass in
               engine/metrics/profiler.py —
               execute_uniqueness_and_categorical(con, handle, report, plan,
               config, registry) consumes the M2-T3 ExecutionPlan, executes
               uniqueness.distinct_count via render_sql (exact template OR
               HLL approx_sql_template chosen by the planner's method label,
               §2.2 — algorithm=HLL stamped via decision.provenance_kwargs()),
               derives uniqueness.is_unique_candidate from distinct_count.value
               == count(col) on the SAME scanned relation (boolean contract
               field has no provenance carrier — honesty rides on the paired
               MetricValue), builds uniqueness.duplicate_groups SQL inline
               (contract_target=None → auxiliary dict
               ProfilerResult.duplicate_group_counts for M2-T8 findings),
               and executes categorical.{frequencies,rare_categories,
               label_inconsistencies} via profiler-built GROUP BY SQL
               (§4 low-cardinality gate reads slot.distinct_count and refuses
               above CATEGORICAL_MAX_DISTINCT=1_000, block ABSENT — never a
               truncated top-K masquerading as the full enumeration).
               Constants CATEGORICAL_TOP_K=50, RARE_FREQUENCY_SHARE=0.001
               documented inline (MVP heuristic; no per-domain code path,
               AGENTS §5). Sampled decisions reuse the M2-T4 one shared
               bounded sample relation (md5 seed ORDER + LIMIT, reservoir
               finalised M2-T10); approximate decisions with no approx
               template (is_unique_candidate under STANDARD-large, label_
               inconsistencies) fall back to EXACT SQL while keeping the
               planner's approximate label — same M2-T3 delegation M2-T4
               established for stddev/quick. §6.4 per-metric duckdb.Error →
               RunErrorEntry(METRIC_NOT_COMPUTED) + honest absence
               (not_computed MetricValue for distinct_count; boolean defaults
               False; categorical arrays stay unset ⇒ block omitted via the
               pydantic null-rejection policy — keys are DROPPED, never
               silently nulled). Section gating: when §4 gate 2 disables the
               uniqueness section, all three uniqueness decisions become
               not_computed and the ColumnProfile.uniqueness block is
               structurally OMITTED (tracked via _UniCatSlot.uniqueness_executed),
               never fabricated with is_unique_candidate=False. +
               tests/test_profilers_uniqueness_categorical.py (27 tests:
               distinct_count vs Polars n_unique + hand, is_unique_candidate
               identifier vs duplicate vs all-null-vacuously-True edge,
               duplicate_group_counts auxiliary parity, HLL approximate path
               with n_rows>LARGE_ROW_LIMIT labelled algorithm=HLL + within
               error bounds, sampled path (seed + bounded + deterministic
               rerun), top-K frequency desc/raw asc + Polars group_by
               parity + NULL exclusion + numeric-column registry gate,
               rare-categories threshold + no-singleton empty, label_
               inconsistencies case+whitespace collapse (DuckDB VARCHAR sort
               is byte-ASCII: 'ACTIVE'<'Active'<'active'), high-cardinality
               gate (>1000 distinct) omits the whole categorical block,
               §4 gate-2 section-off uniqueness omits the whole uniqueness
               block, §6.4 partial failure, empty-relation edges, every
               profile §3.2 schema-valid across STANDARD/QUICK/DEEP +
               byte-stable rerun). 402/402 pytest green, tsc clean
               2026-10-24.
Session notes: M2-T4 delivered engine/metrics/profiler.py —
               execute_stat_and_missingness(con, handle, report, plan, config,
               registry) consumes the M2-T3 ExecutionPlan, renders each
               templated stats/missingness decision via render_sql against the
               DuckDB view (or ONE shared bounded sample relation for `sampled`
               decisions — md5('seed#‖_row_id') ORDER + LIMIT, a preliminary
               reservoir deferred to M2-T10), and stamps scalars into
               ColumnProfile.stats/.missingness as MetricValue with
               Provenance(**decision.provenance_kwargs()). not_computed →
               value-key ABSENT (MetricValue.model_construct + fields_set.
               discard('value')); denominator omitted when None. Sentinel set
               BOUND via executemany($1) into a temp `dpe_sentinels` relation
               read by the new {sentinels} placeholder (ADR-017) — values never
               enter SQL. An `approximate` decision whose spec has no approx
               template (stddev under quick) falls back to EXACT SQL but keeps
               the approximate label (M2-T3 delegated execution detail). §6.4:
               per-metric duckdb.Error → RunErrorEntry(METRIC_NOT_COMPUTED) +
               not_computed value, run continues. DECIMAL fetches normalise to
               float (JSON-safe). ADR-017 (CONTRACTS §9
               profiling.missing.sentinel_values + ARCHITECTURE ADR-017 +
               ENGINE §3 note): dataset_config.schema.json + ConfigMissing
               pydantic + contracts.ts ConfigMissing — exact/case-sensitive
               match, absent ⇒ sentinel_count 0. + registry templates for
               missingness.{sentinel_count,missing_rate} + render_sql(params=…)
               + unsubstituted-placeholder guard. +
               tests/test_profilers_stats_missingness.py (24: stats vs Polars
               std(ddof=1)/quantile(linear) + hand; missingness
               null/blank/sentinel/missing_rate; ADR-017 absent/case-sensitive/
               bound-quote; exact/approximate(t-digest)/sampled(seed+determinism
               +bounded)/not_computed(reason,no value); §6.4 partial-failure;
               empty relation; DECIMAL transport; §3.2 schema-valid + byte-stable
               rerun) + 1 new render_sql placeholder-guard test in
               test_metric_registry_planner. 375/375 pytest green, tsc clean
               2026-10-24.
Session notes: M2-T3 delivered data_profiling_engine/engine/metrics/__init__.py
               (CostClass enum 4 members w/ §2.1 verbatim behaviour; MetricSpec
               frozen dataclass — metric_id/family/contract_target/scope/
               cost_class + applicability (logical+physical) + section +
               approximate/full-relation/exact-under-sampling/cast-on-fetch
               flags + sql/approx templates; 34-row MVP catalog across the 8
               MVP-active §3 profiler families; MetricRegistry immutable w/
               dup+inactive+unknown guards; default_registry()); engine/
               planner.py (PlanDecision enforces §2.2 honesty in __post_init__:
               sampled⇒seed, approximate⇒algorithm, not_computed⇒reason;
               ExecutionPlan.computed_tasks()/skipped(); pure decide_method()
               = the §2.1×§8 table; plan_execution() applies §4 gates
               1(type)/2(sections)/3(mode); render_sql() single place SQL is
               materialised — {table}/{col} quoting, approx swap, temporal
               CAST(... AS VARCHAR) wrap). NO computation lives here yet — that
               is M2-T4..T6. +tests/test_metric_registry_planner.py (64 tests:
               registry integrity, contract_target paths resolved against the
               frozen JSON Schemas via $ref-walk, catalog↔§3 family + §2.1
               cost-class parity, table-driven decide_method vs an independent
               dict reference transcribed from the prose, Provenance stamping,
               selection/section gating, determinism, templated SQL executed on
               a hand-computed DuckDB fixture incl. HLL/t-digest approx +
               TZ CAST + odd-identifier quoting). 350/350 pytest green, tsc
               clean 2026-10-24.
Session notes: M2-T2 delivered data_profiling_engine/discovery/
               (types.py LogicalType+Confidence+ColumnSignals+Discovery+
               Report; signals.py bounded DuckDB aggregates + reservoir
               sample; inference.py cascade + config overlay; __init__.py
               discover(con, handle, config=None)). Config wins: declared
               primary_key forces semantic=identifier (HIGH, config_declared);
               declared units are copied verbatim into ColumnProfile.unit.
               Currency is only `known` when config says so; a name+symbol
               match yields `assumed`; anything else numeric_currency emits
               `unknown` (AGENTS §3 "never present sampled/approx as exact"
               applied to unit status). ColumnProfile skeletons emitted by
               discovery already schema-validate against
               column_profile.schema.json (metric sub-blocks come in
               M2-T4..T6). +tests/test_discovery.py (31 tests: base
               families, VARCHAR pattern promotions email/uuid/url/phone/
               ISO date+datetime, currency-name→numeric_currency, config
               PK+unit overlay, distinct-exact↔HLL switch at 10K, empty/
               all-null edges, DiscoveryError→SCHEMA_INFERENCE_FAILED
               registry code, deterministic re-run).
               Test:  .venv/bin/python -m pytest data_profiling_engine/tests -q
                      (286 passed 2026-10-24; npm --prefix frontend run
                      typecheck clean)
```

## ✔ GOTCHAS ( discoveries every future session must know )

```text
- Use .venv (uv, Python 3.12). System python3 is 3.14 — no pyobjc/duckdb wheels; never use it.
- pywebview: `webview` module has no __version__ attr; don't probe it in tests.
- macOS zsh has no `timeout` cmd; use the runner's own timeout for smoke runs.
- vite `base:'./'` is mandatory — absolute asset paths break under file:// (verified via smoke).
- Smoke entry: `python -m data_profiling_engine.main --smoke` (opens window, auto-closes).
- pywebview `evaluate_js` serialises a JS Promise as `{}` — stash the resolution on `window`
  and poll from Python instead of expecting an awaited return (M0-T2 gotcha).
- pywebview injects `window.pywebview` AFTER page load and only then fires `pywebviewready`;
  the packaged React bundle wins that race, so a render-time `bridgeAvailable()` snapshot is
  always false there. Use `frontend/src/bridge/useBridgeReady.ts` (event + bounded poll) —
  never read the flag once (M7-T5c).
- `ditto src dst` MERGES: a reinstall over an existing `/Applications/DataProfilerEngine.app`
  leaves stale `frontend/dist/assets/*.js` behind (the 13:21 asset survived a 14:44 build).
  `rm -rf` the destination first when the point is bundle identity (M7-T5c).
- The app's window can restore at x=1400 on a 1280-wide screen, so screen-coordinate clicks
  land outside it (`System Events` AX clicks still "succeed"). Move it first:
  `osascript -e 'tell application "System Events" to tell process "DataProfilerEngine" to set {position of window 1, size of window 1} to {{80, 60}, {1120, 740}}'`.
- Driving the packaged UI: `screencapture` full-screen shows whatever is frontmost — use
  `cgui.py winshot` (CGWindowID capture). Quartz `type_text` does NOT reach the WKWebView
  text field; `pbcopy` + System Events `keystroke "v" using command down` does.
- `artifact.open` really opens the OS viewer, and macOS swallows the FIRST click on a
  non-key window (first-mouse). So after every export/reveal the viewer sits above the app
  and the next click is eaten — re-activate and click from ONE process:
  `cgui.py clickapp x y` (separate shell invocations let other apps steal focus).
- Bridge spike codes `BRIDGE_REQUEST_INVALID` (bridge/errors.py) + `JOB_NOT_FOUND`
  (M0-T3) were promoted into the formal §8 registry (`data_profiling_engine/errors.py`,
  ErrorCode members + §8 doc rows) in M1-T6; `bridge/errors.py` now just re-exports
  their `.value`. Add new codes as an ErrorCode member + ERROR_REGISTRY row + §8
  doc row in one change.
- `multiprocessing.context` has NO `BaseEvent` — import `from multiprocessing.synchronize
  import Event`; only `SpawnContext`/`get_context` live in `multiprocessing.context`.
- A handler result dict containing a `job_id` key must NOT be used to detect
  "start accepted" (status polls carry job_id too) — facade uses the private
  `__start_accepted__` sentinel key instead; it is stripped before responding.
- Worker exit after confirmed CANCELLED is async (feeder joins it); tests must poll
  `is_alive()` to false, not assert it immediately after the terminal status appears.
- `freeze_support()` is the first statement of `main()` (module-level `__main__` guard
  alone is not enough for the packaged entry point, ADR-006/009).
- PyInstaller **onedir vs onefile** is decided by presence of `COLLECT()`: passing
  `a.binaries`/`a.datas` straight to `EXE()` (no COLLECT) yields a **onefile** exe that
  extracts to `sys._MEIPASS` at runtime — `main._bundle_root()` reads that when frozen.
- Frozen `spawn` worker works once `data_profiling_engine.bridge.worker.process` is an
  explicit `hiddenimport` (the child re-imports the target module fresh; static analysis
  from the lazy `main→facade→manager→process` chain is not enough). DuckDB ext
  `_duckdb.abi3.so` needs `hiddenimports=['duckdb','_duckdb']` + `collect_dynamic_libs`.
- pywebview 6.x Win backend (`edgechromium`) imports `clr` (pythonnet) + WinForms at
  module level and clr.AddReferences `webview/lib/*.dll` — those DLLs ride inside the
  wheel, already collected by `collect_data_files("webview")`; spec keeps per-OS
  hiddenimports in `platform_hiddenimports` (untested on Win until M0-T5 runs).
- M0-T5 Win result (2026-10-24): platform-aware spec + edgechromium/clr hiddenimports
  passed the frozen smoke on Windows **first try** — no packaging fix needed; keep the
  spec's `platform_hiddenimports` block intact for later milestones.
- Schema-pack convention (M1-T1): every file in `data_profiling_engine/schemas/`
  is named `<snake>.schema.json`, carries a top-level `$id` under
  `https://dpe.local/schemas/`, and cross-refs by that full URI (never `#` for
  a `$def` that might be extracted for standalone validation). Loader is
  `schemas/__init__.py::validator_for(name)`; add a new schema by dropping the
  file — `_load_raw()` picks it up automatically and tests assert `$id` ↔
  filename consistency.
- M1-T1 added `jsonschema>=4.22` to requirements.txt (Draft 2020-12 +
  `referencing`); installed via `uv pip install --python .venv/bin/python`.
- CONTRACTS §2 / §3 examples in the doc use `"..."` placeholders for
  timestamps / hashes; when turning them into pytest fixtures fill in a
  realistic ULID-ish `run_id`, `sha256:<hex>` hash, and RFC-3339 `Z` date so
  FormatChecker + pattern rules pass without disabling checks.
- CONTRACTS §9 exemplar quirk (M1-T3): `config_version` is an **integer** (3),
  unlike persisted objects' string `schema_version` (`^\d+\.\d+$`); and config
  sampling uses key `size` while ProfilingRun.sampling uses `sample_size` —
  don't "align" them without a doc change.
- Pydantic v2 (M1-T4): a **single-underscore class attr becomes a
  ModelPrivateAttr**, so model-level markers must be non-underscore `ClassVar`s
  (we use `NULLABLE_FIELDS` / `NULLABLE_DICT_FIELDS`), never `_NULLABLE`.
- Pydantic emits a default-valued `Optional` field as JSON `null`, which our
  strict schemas reject ("never a silent null"). `ContractModel` overrides
  `model_dump`/`model_dump_json`: untouched-None ⇒ key omitted, explicit-None ⇒
  null only for `NULLABLE_FIELDS`; a `mode="before"` validator rejects explicit
  null on non-nullable declared fields. Open models pass extra keys through.
- Parity harness lives in tests/test_models.py: it compares canonical
  `properties`/`required`/`enum` against `model_fields` per root schema — extend
  it whenever a schema file is added (M1-T6).
- TS↔schema parity lives in tests/test_contracts_ts_parity.py: it line-parses
  `frontend/src/types/contracts.ts` (format is load-bearing — `<Name>Values`
  const tuples, `?`=optional, `| null`=nullable, `[key: string]: unknown`=open,
  one field per line) and asserts root property/required/enum parity vs the 9
  schemas. Keep the TS file in that format or the parser silently under-matches.
  GOTCHA: an open interface's index signature MUST be `unknown`, not
  `JsonValue` — a sub-interface with optional props (e.g. `TemporalRange`) is
  NOT assignable to a `JsonValue` index (tsc TS2411); `unknown` keeps tsc green
  and the parity check only tests for the index signature's presence.
- `con.execute()` a second time on the same DuckDB connection **replaces the
  open result** — snapshot `rel.description` immediately after execute, or the
  later probe silently mutates what you read (M2-T1 gotcha).
- DuckDB 2.0 `read_csv` has **no `file_row_number` option** ("not supported by
  this reader") — CSV row-ids use `row_number() OVER ()` (sequential scan,
  1-based data-line); `read_parquet(file_row_number=true)` is 0-based global
  row-group+offset (M2-T1).
- Plain `polars` wheel crashes this Mac (no AVX2, illegal instruction at
  import) — installed `polars[rtcompat]` (polars-runtime-compat wheel);
  requirements.txt pinned `polars[rtcompat]>=2.0`. Polars 2.0 API: pass str
  paths, not Path.
- DuckDB `TIMESTAMP WITH TIME ZONE` scalar fetch into Python requires `pytz`
  (DuckDB builds a tz-aware datetime on fetch); we do not ship pytz. Any
  min/max/first-style scalar probe on a temporal column MUST `CAST(... AS
  VARCHAR)` in SQL (see discovery/signals.py collect_column_signals). Same
  for M2-T6 temporal profiler — keep it CAST-clean or add pytz deliberately.
- DuckDB physical type strings are prefix-matched in `_family`; check
  `INTEGER[]` / `STRUCT(...)` / `MAP(...)` / `UNION(...)` **BEFORE** the
  integer/float families or `INTEGER[]` mis-detects as integer (M2-T2 gotcha).
- Pydantic `use_enum_values=True` on the contract base classes means
  `profile.semantic_type` / `unit.status` are plain strings at runtime, not
  Enum members — compare with `== UnitStatus.KNOWN.value`, never `.value` on
  the returned field (M2-T2 test gotcha).
- ColumnProfile + DatasetProfile are `additionalProperties:false`; the
  discovery "confidence + rationale" required by ENGINE §4.1 ("recorded with
  a confidence so misclassification is visible") lives on the engine-internal
  ColumnDiscovery object, not the contract. Persisted output stays clean;
  planner/findings read the internal view. No contract change needed until
  a UI surface asks for it — then a new ADR (M2-T2 design note).
- Planner §2.1×§8 gating: an `expensive_exact` metric stays `exact` at small N
  under standard (a bounded DISTINCT hash is cheap) and only degrades to
  approximate when `n_rows > LARGE_ROW_LIMIT` OR mode is `quick`; `deep` forces
  exact (and refuses sampling for `requires_full_relation` rows ⇒
  not_computed+reason). `prohibited_at_scale` is never exact — sampled iff a
  sampling window exists else not_computed+reason. `decide_method()` is the
  pure table; `plan_execution()` layers §4 gates on top. Reference test compares
  against an INDEPENDENT dict literal transcribed from the prose, not the
  planner's own branches (M2-T3).
- `MetricSpec.contract_target` dotted paths (e.g. `column_profile.stats.mean`,
  `dataset_profile.primary_key_candidates`) are validated by resolving them
  against the JSON Schema `properties` (recursive `$ref` walk of
  `validator_for(name).schema`), NOT against pydantic `model_fields` — the
  `Optional[...]` annotations on models hide the sub-model type so field-walking
  is unreliable; the schema is the frozen source of truth (M2-T3).
- DuckDB `quantile_cont` linear-interpolation hand-values over {1,2,3}: p25=1.5,
  median=2.0, p75=2.5 (NOT nearest-rank); `avg` ignores NULLs (3,1,2→2.0). Fixed
  these reference literals while writing the M2-T3 template-execution test.
- DuckDB `read_csv` reads an **empty quoted field `""` as NULL**, not the blank
  string `''` (default null_str is empty) — to exercise `missingness.blank_count`
  you must build the fixture as a DuckDB `VALUES` relation with a real `''`, not
  a CSV (M2-T4 test gotcha).
- Strict contract models reject an **explicit null on a non-nullable field**
  (`MetricValue.denominator` included) — when a metric is absent OMIT the key,
  never pass `None`; for `not_computed` MetricValue use `model_construct` then
  `model_fields_set.discard('value')` so the canonical dump omits `value`
  (M2-T4).
- Planner `decide_method` labels a `supports_approximate` metric `approximate`
  even when it has NO `approx_sql_template` (e.g. `stats.stddev` under quick);
  `render_sql` then raises "no approx template". The PROFILER must fall back to
  rendering the EXACT template while KEEPING the planner's `approximate`+algorithm
  provenance label (M2-T3 delegated this execution detail to M2-T4..T6).
- Sentinel values cross the SQL boundary **bound, not interpolated** (ADR-017):
  the missingness runner `executemany($1)`-loads them into temp relation
  `dpe_sentinels` and `render_sql(params={'sentinels': '"dpe_sentinels"'})`
  substitutes the {sentinels} placeholder; a sentinel containing a quote (`it's`)
  still matches literally and cannot break the statement.
- ColumnProfile **sub-blocks with no provenance carrier on a required field**
  must be OMITTED when the planner skipped the family, never fabricated with
  a default value. M2-T5 case: `uniqueness.is_unique_candidate` is a plain
  `bool` — when §4 gate 2 disables the `uniqueness` section, all three
  uniqueness decisions become not_computed and the whole
  `ColumnProfile.uniqueness` block must be absent (tracked by the
  runner-internal `_UniCatSlot.uniqueness_executed` flag), not emitted with
  `is_unique_candidate=False` (that would hide a legitimate skip as a
  "not unique" verdict). Same rule for `categorical`: unset array fields on
  the open `Categorical` block must be DROPPED from the constructor kwargs
  (ContractModel still rejects an explicit null on a non-nullable field even
  for `_OpenModel`, same as M1-T4 `Provenance.denominator`) (M2-T5).
- DuckDB `VARCHAR` sort is **byte/ASCII, not locale-aware** — uppercase sorts
  BEFORE lowercase: `'ACTIVE' < 'Active' < 'active'`. Any reference test that
  asserts a raw-variants ORDER BY must expect this order, not Python's
  `sorted(...)` (which happens to agree for pure-ASCII but not for e.g.
  case-folded Unicode) (M2-T5 test gotcha).
- Low-cardinality categorical gate: ENGINE §3 lists Categorical Profiler as
  computing "frequency, cardinality, rare categories" but the §4 registry
  only gates the FAMILY by logical type (`_STRINGISH_LOGICAL`); the actual
  runtime skip on a high-cardinality string (UUID/EMAIL near-unique) MUST
  happen in the M2-T5+ runner reading `uniqueness.distinct_count` from the
  same pass — the planner is size-agnostic here because `decide_method`
  doesn't know per-column cardinality at plan time. Above
  `CATEGORICAL_MAX_DISTINCT` (1_000 MVP heuristic) the whole
  `ColumnProfile.categorical` block is omitted, never a truncated top-K
  masquerading as a full enumeration (ADR-015 honesty) (M2-T5).
- DuckDB 2.0 has **NO `width_bucket` scalar function** (CatalogException on
  use). Histogram equal-width binning must be emulated with the identical
  formula: `CAST(LEAST(nb, FLOOR((col - lo) / (hi - lo) * nb) + 1) AS INTEGER)`
  over non-null values (NULLs fall out of the WHERE), top edge `hi` folds into
  the last bin via the `LEAST`; guard the `hi == lo` (constant) divide-by-zero
  with a single-bin shortcut BEFORE the bucket query (M2-T6).
- DuckDB `skewness()` returns the **adjusted Fisher–Pearson G1**
  (`g1 * sqrt(n(n-1))/(n-2)`), while Polars `.skew()` returns the **biased g1**
  (`m3 / m2**1.5`) — they differ by the sample-size correction factor. A
  reference test must compare against G1 computed in plain Python, and
  explicitly assert it DIFFERS from Polars (the engine follows DuckDB). Both
  are undefined for n<3 or zero variance → honest `not_computed` (M2-T6).
- A contract `Provenance` (or any pydantic model) nested **inside an OPEN dict
  value** (e.g. `Temporal.volume_per_granularity['buckets'][i]['provenance']`,
  `gaps['provenance']`) is NOT auto-serialised by `ContractModel.model_dump
  (mode='json')` — the open `Dict[str, Any]` passes the raw object through and
  `model_dump_json`/JSONSchema validation then raises `Object of type
  Provenance is not JSON serializable`. Call `.model_dump(mode='json')` on the
  nested Provenance yourself before storing it in the open block (M2-T6;
  out_of_expected_range already followed this pattern).
- Under `use_enum_values=True` a `DatasetConfigRule.rule`/`.severity`/
  `.dimension` is a **plain string** at runtime, so `rule.rule is RuleType.X`
  is ALWAYS False (M2-T2 gotcha generalised). The §5.1 catalog dict still
  looks up fine (a str-Enum hashes/equals its value), but *dispatch* on the
  registry's real enum member (`defn.rule_type`) or compare with `==`, never
  `is`. Same trap for `Severity.WARNING` in the M2-T7 WARN policy. (M2-T7)
- `unique`-rule violations = `non_null_rows − distinct_non_null_values` (a
  per-group "minimum rows to drop to make unique"), NOT a per-row
  `SUM(grp_n-1)` window which double-counts every duplicate group (Active×3 +
  Closed Won×3 summed the window to 12 instead of the correct 4). The
  `COUNT(*) OVER (PARTITION BY col)` is still fine for the *sample refs*
  (`grp_n > 1`), just not for the count. (M2-T7)
- ENGINE §5.2 fixes the rule statement shape as `(violations, total,
  sample_row_ids)` in **one** statement. Keep it memory-flat with a bounded
  `array_agg(rid) FROM (SELECT rid … ORDER BY rid LIMIT 200)` subquery, and
  give a CTE-based (unique/outlier) SELECT **no outer FROM** so an empty
  relation still yields exactly one aggregate row. Rule-level `missing` =
  `null OR trim=''` only — sentinels (ADR-017) belong to the ColumnProfile
  metric, deliberately NOT the rule's `missing_rate_max`. (M2-T7)
- Under `use_enum_values=True` a **persisted contract enum field is a plain str
  at runtime, not an Enum member** — a `QualityCheckResult.dimension`/`.status`/
  `.severity` or `CheckThreshold.operator` reached by a downstream consumer has
  NO `.value` (calling it raises AttributeError). Read such fields through a
  `getattr(x,'value',x)` helper (`findings/detectors.py::_val`), and rely on
  str-Enum `==`/hash for dict lookups and `in` membership (a `"FAIL" in
  (CheckStatus.FAIL, ...)` works). Freshly-constructed enums (Severity.WARNING,
  QualityDimension.X) still carry `.value`, so only persisted-then-read fields
  bite. (M2-T8)
- GIT-HISTORY NOTE: the M2-T5 session left its work UNCOMMITTED (last commit
  was `abe0c56 M2-T4`); profiler.py's M2-T5 uniqueness/categorical code + the
  M2-T5 test file were still only in the working tree when M2-T6 ran. Because
  the M2-T5 and M2-T6 edits share one file and cannot be cleanly hunk-split,
  M2-T5's artifacts land in the M2-T6 commit. STATE.md's M2-T5 `[x]` predates
  this commit — do not assume a `[x]` task has its own commit; verify with
  `git log` when resuming.
- A strict `ContractModel` only emits a field in `model_dump` when it is in
  `__pydantic_fields_set__` OR is a non-None value it was given; to set an
  OPTIONAL field (e.g. `Finding.recommendation_ids`, left `[]` by M2-T8) on an
  already-constructed model WITHOUT re-running every validator, do
  `Model.model_validate({**obj.model_dump(), "field": value})` — a plain
  `model_copy(update=...)` is NOT guaranteed to register the key in
  `fields_set`, so the strict `_dump()` would omit it. The round-trip is safe
  because these contract models are `extra=forbid` with self-contained dumps
  (M2-T9).
- The bounded sample relation `_sample_relation` (M2-T10) is a **real seeded
  reservoir** — `SELECT * FROM "view" USING SAMPLE RESERVOIR (size ROWS)
  REPEATABLE (seed)` (DuckDB; deterministic per seed, differs across seeds,
  returns-all when n<size), NOT the old md5 ORDER+LIMIT hash. `block` = a
  bounded `LIMIT` scan for locality. GOTCHA: because the name is content-hashed
  on (view,strategy,size,seed), `run_profiling_pipeline` builds the SAME window
  name in all three profiler passes; the defensive pre-drop MUST be
  `DROP TABLE IF EXISTS` (a prior pass left a TEMP TABLE of that name —
  `DROP VIEW IF EXISTS` raises `CatalogException: ... is of type Table`).
  `USING SAMPLE` works on a VIEW, not only tables (M2-T10).
- `orchestration.run_profiling_pipeline` composes the three M2-T4/T5/T6 family
  passes by MERGING their disjoint ColumnProfile blocks with
  `profile.model_copy(update={block: value})` starting from the
  discovery-skeleton — the passes each return a copy of the SAME base, so the
  merge is by-index over `report.columns` and only copies a block that a pass
  actually populated (None stays absent). `config.profiling.mode` is a plain
  str under `use_enum_values`, re-hydrate with `ProfileMode(...)` before
  `plan_execution` (it identity-compares against the enum) (M2-T10).
- Assembling a rollup contract from Python values hits the SAME explicit-null
  trap as M2-T4 but via the CONSTRUCTOR: `DatasetProfile(row_count=None,...)`
  raises `field 'job_id' does not permit null` for every optional field the
  canonical schema does NOT type nullable. Build them through
  `orchestration/rollup.py::_instantiate(cls, **kw)` — it drops `None` values
  whose key is not in `cls.NULLABLE_FIELDS` (those are OMITTED) and keeps an
  explicit `None` for genuinely-nullable keys (dumped as valid JSON null), then
  `cls.model_validate(data)`. `job_id`/`sampling`/`result_refs`/`error_log` and
  `row_count`/`column_count`/`primary_key_candidates` all ride this path
  (M2-T11).
- `run_dataset_profiling` on a REAL CSV (not an in-memory VALUES relation) is
  ~13s in standard mode for just 200 rows × 5 cols — the cost is discovery +
  the full metric fan-out, NOT DuckDB scan. `test_cli_rollup.py` therefore
  shares ONE module-scoped `standard_bundle` fixture across every read-only
  assertion (the pipeline dominates wall-clock); tests needing a *different*
  config (sampling/error/CLI/parquet) build their own. Also: `--config` uses
  `yaml.safe_load` (needs `pyyaml`, added to requirements.txt); the venv is
  uv-managed so install with `uv pip install "PyYAML>=6.0"
  --python .venv/bin/python` — there is no `.venv/bin/pip` (M2-T11).
- `sqlite3.executescript()` **implicitly commits** — it destroys the atomicity of
  a migration and fights an explicit `BEGIN` (legacy `isolation_level=""`
  auto-commits around it). For a transactional DDL step use
  `conn.isolation_level = None` + manual `BEGIN IMMEDIATE` → per-statement
  `execute()` loop → stamped `COMMIT`, `ROLLBACK` on `sqlite3.Error`, and
  restore `isolation_level` in `finally` (M3-T1, `migrations.apply_migrations`).
- Splitting a DDL script on `;` must strip comments **per line first**: a
  trailing `--` comment that contains a `;` (e.g.
  `-- NULL = no §2 sampling block; 0 = declared off`) cuts the following
  statement in half → `sqlite3.OperationalError: incomplete input`. Use
  `"\n".join(line.split("--", 1)[0] for line in sql.splitlines())` before the
  split (`migrations._split_statements`).
- SQL `NULL` silently MERGES "key absent" with "explicit null", which breaks the
  `ContractModel` round-trip (§1 treats the two as different meanings for metric
  values). Store JSON-typed fields through `metadata_store._field_json(model,
  name)` (returns `None` only when the key was never set; an explicit null is
  kept as the text `"null"`) and rebuild with `_apply_json_field(...)`. Needed
  wherever a `MetricValue.value`/`CheckThreshold.value`/`FindingFact.value`
  round-trip must be exact. Plain scalar nullable columns keep the benign
  equivalence (`_build_model` + `NULLABLE_FIELDS`).
- A 2-state `INTEGER NOT NULL` flag also collapses "block absent" (a §2 run with
  NO `sampling` block) into "declared off" — make the column NULLable and map
  `None / 0 / 1` explicitly, both in writer and reader (`runs.sampling_enabled`,
  M3-T1).
- `Recommendation` (§4.3) has **no** `run_id` field, and `ProfileBundle.errors`
  is NOT what gets persisted — per-task errors ride `run.error_log`; the store's
  `run_errors` rows come from there. Writing either from the bundle is a bug
  (M3-T1 test review).
- Metadata-store failures must surface as `MetadataStoreError` carrying a §8
  `ErrorCode` (`METADATA_WRITE_FAILED`, retryable, source `storage`) — never a
  bare `sqlite3.Error`, which would leak an unusable/PII-bearing message through
  the bridge. Adding it required the §8 triple in ONE change: `ErrorCode` member
  + `ERROR_REGISTRY` row + `docs/CONTRACTS.md` §8 table row, plus the
  `_DOC_RETRYABLE` map in `tests/test_bridge_envelopes.py` (M3-T1).
- `MetadataStore` write ops must go through `_write_tx` (one short
  `BEGIN IMMEDIATE`): `journal_mode=WAL` + `busy_timeout` make a *second*
  connection on the same file a valid concurrent reader (that is the ADR-008
  "read during run" test shape); one connection per thread/process, never
  shared across threads.
- M5-T2 gotchas (bridge ↔ store ↔ worker, verified 2026-10-25):
  * `MetadataStore` is one-connection-per-thread BY DESIGN, and the manager's
    `_persist` runs on a per-job **feeder thread** — it MUST NOT reuse the main
    thread's handle; it opens a short-lived `MetadataStore(db_path=..., migrate=False)`
    per call for on-disk stores and only touches the shared store directly when
    it is `:memory:` (single-threaded test path).
  * Canonical `JobStatus`/`JobProgress` are strict `ContractModel`s (explicit
    null rejected) while the spike runtime model keeps null keys — always fold
    via `manager._canonical_status` BEFORE `save_job_status` (omit `current_step`
    when empty, `error_code`/`result` when None).
  * `jobs.dataset_id` has an FK to `datasets`: in `_launch`'s persist branch
    `register_dataset` MUST precede `create_job`.
  * `bridge/runtime.py` uses `RLock` (not `Lock`): `get_profile_manager` nests
    `get_metadata_store`; both mutate module globals and need `global` decls.
  * `BridgeFacade` snapshots `self._commands` at construction — tests that
    monkeypatch a handler patch the INSTANCE's `_commands`, not module COMMANDS.
  * Spike-only commands (`echo`, `job.*` spike path) are registered only with
    `enable_spike_commands=True` (or env `DPE_ENABLE_SPIKE_COMMANDS=1`); the
    packaged facade leaves them OFF so `echo` ⇒ COMMAND_UNKNOWN. Spike test
    fixtures must pass the flag (`test_bridge_facade.py` fixture does).
  * §6.5 DuckDB mid-query interrupt **IS implemented** (M7-T5 run 2):
    `profile_job.py` owns the connection and a watchdog calls
    `DuckDBPyConnection.interrupt()` 0.2 s after the flag flips (measured ~20 ms
    to abort a running query); the engine's stage `checkpoint` covers the
    between-passes case. The pre-M7-T5 note here said the opposite
    ("cancel is checked at STEP boundaries only … Phase-3 depth item") — that was
    wrong against CONTRACTS §6.5, and it is why the packaged cancel took
    4 min 18 s before the fix. GOTCHA: `con.interrupt()` raises
    `duckdb.InterruptException` in the compute thread, so the worker must
    classify it with `_interrupted_by_cancel` (cancel flag AND type) — otherwise
    a genuine DuckDB failure that races a cancel would be excused as CANCELLED.
    Second trap: `engine/metrics/profiler.py` catches `duckdb.Error` per metric
    (correctly — §6.4 partial failure), so ONE `interrupt()` only kills ONE
    query and the pass runs on. The watchdog must re-interrupt on every poll
    while the flag stands, or cancel latency silently stays minutes long.
- M5-T1 read-command gotchas:
  * `MetadataStore.load_profiling_run` preserves absence fidelity
    (`_present_keys` / `_retain_present`): a key the stored document never
    carried comes back ABSENT — so `result_refs` is only readable when the run
    document was built WITH them. Any caller that wants `report.get_summary`
    refs must pass `result_refs=refs` into `profile_dataset` BEFORE `save_run`
    (that is what `bridge/worker/profile_job.py` does; the test fixture now
    mirrors it). Do not "fix" the reader — the behaviour is intentional.
  * `_query_one` returns a dict row, never a tuple: `SELECT COUNT(*)` must be
    aliased (`COUNT(*) AS n`) and read as `row["n"]`; `row[0]` raises.
  * §8 has no run-address code and is frozen, so an unresolvable
    `run_id`/`dataset_id` in a read command returns `BRIDGE_REQUEST_INVALID`
    (same pattern as `JOB_NOT_FOUND` for an unknown job). Adding `RUN_NOT_FOUND`
    would be a contract change: doc row + `ErrorCode` + registry row + TS enum.
  * `ErrorCode.DATASET_READ_FAILED` is `retryable=True`, and the facade's
    `_error()` now reads retryability from the registry — a missing source file
    legitimately surfaces `retryable: True`. Tests asserting `retryable is
    False` must use a non-retryable code (`CONFIG_INVALID`, `BRIDGE_*`).
  * `dataset.preview` must drop the connector's `_row_id` plumbing column
    (ENGINE §5.2) and must render rows BEFORE column descriptors, or the
    `masked` flags miss cell-level PII discovered while masking.
  * Adding TS interfaces to `contracts.ts` is safe for parity, but a new
    `<Name>Values` enum bumps the hard-coded set in
    `test_contracts_ts_parity.py` (19 since M5-T3 mirrored `job_status`) —
    mirror shapes as interfaces, and change an enum only in the same commit
    that updates that set + `ROOT_INTERFACES`.
  * The parity parser needs ONE field per line ending in `;` — a trailing
    `// comment` after the semicolon makes the field invisible to `_FIELD_RE`
    and the root then fails `test_top_level_property_names_match_canonical`.
    Put such notes in the section comment above the interface.
- M5-T3 progress-push gotchas (verified 2026-10-25):
  * `JobManager._notify` must be called while `self._lock` is held and NEVER
    invokes a listener itself — only the dispatcher thread may call out, so a
    stalled/closed WebView cannot block a feeder thread or a job transition.
  * A new status transition MUST publish via `_commit(record)` (7 call sites
    today), never via `_persist` alone — `_persist` is now called only from
    `_commit`, so a site that skips it silently loses its push.
  * The polled status now INCLUDES `job_id` (both transports emit the identical
    object); do not re-add a `pop("job_id")` in the facade.
  * The dispatcher is a daemon thread that lives for the whole process (it
    never exits, `while True: _wake.wait()`); only managers WITH a listener
    spawn one, which is exactly one in the packaged app. Tests that rebind
    `configure_runtime` therefore leave idle threads behind — harmless, but do
    not expect a manager to be garbage-collectable.
  * `PAYLOAD_CAP_BYTES` moved to `bridge/envelope.py` (§5.4 owns all its limits)
    and is re-exported from `facade` — importing it from either still works.
  * `runtime.configure_status_push` is process-global and deliberately survives
    `configure_runtime`; forgetting to clear it in a fixture leaks the sink
    into other tests (the `data_root` fixture in `test_bridge_progress_push.py`
    resets both).
- pywebview v6 `create_file_dialog` is a method on the Python `Window` object,
  NOT automatically injected into JS. `util.inject_pywebview` reflects only the
  public methods on the `js_api` instance (our `BridgeFacade`). To add a native
  "Browse" button to the UI: either add a typed method to the facade that
  invokes `window.create_file_dialog(...)` on the Python side and returns the
  selected path to JS, or trigger the dialog from a Python-side hook and push
  the path via `evaluate_js`. The text-input path entry shipped in M6-T1 is the
  interim until this is done.
- M6-T2 client §9 mirror (`frontend/src/config/validation.ts`) has NO pytest
  parity guard (no frontend test runner by design). If `dataset_config.schema.json`
  or the §9 exemplar ever changes, re-verify the mirror MANUALLY: bundle with
  `npx esbuild src/config/validation.ts --bundle --format=esm` and run the
  exemplar + known-bad mutations through `validateDatasetConfig`, then diff
  against `S.validate("dataset_config.schema.json", …)`. The server check
  (CONFIG_INVALID at profile.start) is always the authority — a stale mirror
  only mis-hints the UI, it can never admit a bad config.
- profile.get_status polls and __dpeJobStatus pushes return the SAME unwrapped
  §5.3 JobStatus object — `bridgeSend` already strips the envelope, so
  `status.result` (the worker's bounded `_bounded_result`: run_id, dataset_id,
  result_refs, counts) sits DIRECTLY on it; never re-wrap. Merging both
  transports needs a monotonic state-rank gate (a slow poll CAN arrive after a
  newer push). JOB_NOT_FOUND surfaces as a thrown BridgeError (handler
  __bridge_error__ → error envelope), not a status object — catch-and-stop.
```

## M0 — Desktop Spike (gate: BLUEPRINT §5 Phase 0; acceptance: TESTING §5.2)

- [x] **M0-T1** Scaffold: repo layout per ARCHITECTURE §3; pywebview shell loading React **HashRouter** build from local files (ADR-003/010/013). *Spec: ARCHITECTURE §2–3; ADR-013.* ✅ macOS smoke 2026-10-24 (Windows re-verify folds into M0-T5) — commit `5b3d4a0`
- [x] **M0-T2** Bridge hello-world: typed facade stub with one echo command; pydantic request validation (ADR-005). *Spec: CONTRACTS §5.1–5.3; SECURITY §3.* ✅ macOS round-trip via `--smoke` + 20/20 pytest (validation, allow-list, 1 MiB cap, no input echo) 2026-10-24 — commit `a4ef86f`
- [x] **M0-T3** Worker process: `multiprocessing` spawn + echo job + progress via status; `freeze_support()`. *Spec: CONTRACTS §6; ARCHITECTURE ADR-006.* ✅ macOS: facade worker round-trip via `--smoke` (SUCCEEDED, 4/4 steps) + 35/35 pytest ×3 (start/progress/cancel/crash→WORKER_CRASHED/timeout→JOB_TIMEOUT, §6.1 states, accepted+job_id envelope) 2026-10-24 — Windows re-verify folds into M0-T5
- [x] **M0-T4** PyInstaller: macOS build passes M0-T1..T3 in packaged form; DuckDB native lib collected. *Spec: SECURITY §6.2; ADR-009.* ✅ macOS onefile `./dist/DataProfilerEngine --smoke` (2026-10-24): window+`file://` from `_MEIPASS`, bridge echo round-trip, spawn worker `SUCCEEDED 4/4`, DuckDB `SELECT 40+2 → [(42,)]`; 35/35 pytest still green. Spec=`data_profiling_engine.spec`; `build//dist/` gitignored. — Windows re-verify folds into M0-T5
- [x] **M0-T5** Windows build (native env): same gate incl. WebView2 detection. **→ GATE M0: all five green on both OS; else revise ADRs, do not proceed.**
      ✅ Win10/11 x64 native smoke 2026-10-24 (`smoke_win.txt`): WEBVIEW2_RESULT pv=155.0.4283.62 · DUCKDB 42 · WORKER SUCCEEDED 4/4 (frozen spawn) · BRIDGE echo ok · SMOKE_OK `file://` from `_MEIPASS`. **GATE M0 PASSED — both OS green.**

## M1 — Contracts Freeze

- [x] **M1-T1** JSON Schemas in `schemas/`: Provenance, ProfilingRun, DatasetProfile, ColumnProfile. *Spec: CONTRACTS §1–3.* ✅ 4 Draft-2020-12 files under `data_profiling_engine/schemas/` + loader (`schemas/__init__.py`, `referencing.Registry`, `validator_for`/`validate`) + 27-test pack (`tests/test_schemas.py` covering §1.1 method rules, §2 terminal-status/sampling seed, §3.1 columns array, §3.2 unit.status/semantic_type enums, not_computed⇒no-value). 62/62 pytest green 2026-10-24.
- [x] **M1-T2** JSON Schemas: QualityCheckResult, Finding(+fingerprint), Recommendation, FindingWorkflow. *Spec: CONTRACTS §4.* ✅ 4 Draft-2020-12 files added under `data_profiling_engine/schemas/` (§4.1/4.2/4.5/4.3) + `finding` embeds `finding_workflow` via `$ref` + `observed`/facts reuse Provenance `metricValue`. 27 new tests (§4.1 ERROR↔error coupling + status/dimension enums, §4.2 fingerprint `^sha256:[0-9a-f]{64}$` + facts/evidence_refs minItems 1 + hypothesis status enum + workflow null↔object, §4.3 `reason_type=evidence` ⇒ non-empty evidence_refs + status enum, §4.5 comment shape). 89/89 pytest green 2026-10-24.
- [x] **M1-T3** Dataset-config JSON Schema + validator (rejects unknown rule keys, missing `dimension`). *Spec: CONTRACTS §9; ENGINE §5.1.* ✅ `dataset_config.schema.json` (Draft 2020-12, `$id` convention, `$defs/rule` extractable) — hard-rejects unknown config/rule keys via `additionalProperties:false`, closed ENGINE §5.1 rule catalog, `dimension` required (§4.1 enum parity), sampling enabled⇒strategy+size+seed, per-rule-type conditionals (`condition`⇒conditional_not_null; value|threshold⇒threshold rules); §9 YAML exemplar transcribed verbatim as positive test. +24 tests, 113/113 pytest green 2026-10-24.
- [x] **M1-T4** Pydantic models generated/hand-aligned to schemas + round-trip tests. *Spec: CONTRACTS §1.* ✅ `schemas/models.py` — hand-aligned mirror of all 9 frozen schemas (schemas stay source of truth): 17 closed enums, `ContractModel` base enforcing the null/absence policy + strict-vs-open extras, conditional rules as validators (§1.1 sampled⇒seed / approximate⇒algorithm / not_computed⇒reason, §2 sampling lock + terminal status, §4.1 ERROR⇔error coupling, §4.3 evidence⇒refs, §9 rule catalog + conditionals, ADR-016 fingerprint pattern). +49 tests in `tests/test_models.py` (round-trip over all 9 schemas, field/required/enum parity, negative enforcement). 162/162 pytest green 2026-10-24.
- [x] **M1-T5** TS types mirroring contracts + parity test against JSON Schemas. *Spec: CONTRACTS §1; AGENTS §3.* ✅ `frontend/src/types/contracts.ts` — hand-aligned TS mirror of `models.py` (17 `*Values` const-enum tuples + unions, 42 interfaces; `?`/`| null`/`[key: string]: unknown` carry optional/nullable/open semantics; HashRouter build intact) + `tests/test_contracts_ts_parity.py` (14 tests: root property-name + required-set + strict/no-index parity, root-enum↔named-TS-enum order-exact, PascalCase type-reference resolution, all-17-enums). `tsc --noEmit` clean + `vite build` green. 176/176 pytest green 2026-10-24.
- [x] **M1-T6** Error-code registry module + bridge/job envelope models. *Spec: CONTRACTS §5.3, §8.* ✅ `data_profiling_engine/errors.py` — formal §8 registry: `ErrorCode` enum + `ERROR_REGISTRY` (code→layer/meaning/retryable) + `lookup`/`is_retryable`/`is_registered`; promotes `BRIDGE_REQUEST_INVALID`+`JOB_NOT_FOUND` (added §8 doc rows). `bridge/errors.py` re-exports `.value` aliases (facade/manager unchanged). 4 canonical §5.3 envelope schemas + models in `schemas/models.py` — `BridgeRequest`/`BridgeResponse`(status-discriminated ok↔result / accepted↔job_id / error↔error, request_id may be "" for pre-envelope failures)/`BridgeErrorObject`(§8 uppercase code)/`JobStatus`+`JobProgress`(§6.2 6 states). `bridge/envelope.py` re-exports canonical `Bridge*`; spike worker runtime kept enum-based (identity+`is_terminal`), asserted schema-conformant. Registered in `REQUIRED_SCHEMAS` + `test_models.py::CANONICAL_ROOTS`. +`tests/test_bridge_envelopes.py` (45 tests). 230/230 pytest green, tsc clean 2026-10-24. **→ GATE M1 CONTRACTS FREEZE PASSED.**

## M2 — Engine CLI (spine; no UI)

- [x] **M2-T1** Connectors: CSV + Parquet → DuckDB relation, read-only, `content_hash`, row-id policy. *Spec: ENGINE §5.2 note; CONTRACTS §2.* ✅ `connectors/` — `base.py` (pluggable `Connector` ABC + `RelationHandle` + §8-coded `DatasetReadFailed`/`SourceNotAllowed`; pre-compute path allow-list + suffix guard (ADR-011 closed set), streaming sha256 `SourceSnapshot` (+`row_id_policy` extra, open-model §2), LIMIT-0 dual probe = fail-fast parse + (name, physical type) schema w/o rows, collision guards for `_row_id`/`file_row_number`), `csv_connector.py` (`row_number() OVER ()` = generated line number), `parquet_connector.py` (`file_row_number+1` = row-group+offset, plumbing stripped), `__init__.py` registry + suffix dispatch. View = read-only explicit projection + `_row_id` (scan-time eval ⇒ stable under filter, verified). +`tests/test_connectors.py` 25 tests (independent refs: hashlib/Polars/hand-computed; multi-rowgroup 300K offset check; snapshot dump → profiling_run schema-valid; read-only byte/mtime assertions). 255/255 pytest green 2026-10-24.
- [x] **M2-T2** Discovery: physical→logical/semantic type inference + unit status. *Spec: ENGINE §4.1.* ✅ `discovery/` — `types.py` (closed LogicalType enum 18 members + Confidence high/medium/low + ColumnSignals bounded-aggregate dataclass + ColumnDiscovery + DiscoveryReport); `signals.py` (`collect_column_signals` — one scalar-aggregate pass + one bounded reservoir DISTINCT sample for VARCHAR; exact vs HLL switch at 10K; `DiscoveryError` carries §8 code `SCHEMA_INFERENCE_FAILED`); `inference.py` (physical→logical base map, VARCHAR pattern refinement email/uuid/url/phone/ISO-date(-time)/currency-shaped numeric via regex ≥90%HIGH/≥70%MEDIUM, name heuristics `_id`/`_at`/`amount|price|value`/`status|state`/`is_/has_`/freeform, length+cardinality → categorical_nominal / text_freeform / near-unique identifier, numeric low-cardinality → dimension, config overlay: PK ⇒ semantic=identifier HIGH + config_declared=True, units copied verbatim); `__init__.py` (public `discover(con, handle, config=None)`). Unit policy per ENGINE §4.1: `known` ONLY via config, `assumed` via currency symbol, else `unknown`; non-currency ⇒ no unit block. +`tests/test_discovery.py` 31 tests (physical family base, pattern promotions, name nudges, config overlay PK/units, distinct-exact↔HLL switch across 10K, empty/all-null edges, DiscoveryError→registry code, Parquet roundtrip, determinism, schema-valid dump for every column_profile emitted). 286/286 pytest green, tsc clean 2026-10-24.
- [x] **M2-T3** Metric registry core + planner cost classes. *Spec: ENGINE §2, §2.1.* ✅ `engine/metrics/__init__.py` — `CostClass` (4 §2.1 members, large-N behaviour in doc), `MetricSpec` frozen dataclass (metric_id/family/display/contract_target/scope/cost_class + §4 applicability [logical+physical] + section + supports_approximate/requires_full_relation/exact_under_sampling/cast_varchar_on_fetch flags + sql/approx templates), 34-row MVP catalog over the 8 MVP-active §3 families (relationship/relational/drift intentionally unregistered — §3 marks them Phase-2/4), immutable `MetricRegistry` (dup/non-MVP/unknown guards) + `default_registry()`. `engine/planner.py` — `PlanDecision.__post_init__` structurally enforces §2.2 sampling honesty (sampled⇒seed, approximate⇒algorithm, not_computed⇒reason) → `provenance_kwargs()` stamps straight into contract `Provenance`; pure `decide_method()` = the §2.1×§8 table; `plan_execution()` applies §4 gates 1/2/3; `render_sql()` is the single SQL-materialisation point ({table}/{col} quoting, approx swap, temporal CAST-to-VARCHAR for the no-pytz TZ probe). No computation executed here (that is M2-T4..T6). +`tests/test_metric_registry_planner.py` 64 tests (registry integrity; every `contract_target` path resolved against the frozen JSON Schemas by $ref-walking `validator_for(...).schema`; catalog↔§3-family + ↔§2.1-cost-class parity; table-driven `decide_method` vs an **independent** dict reference transcribed from §2.1/§8 prose; Provenance stamping; selection + section gating; determinism; templated SQL executed on a hand-computed DuckDB fixture — exact/HLL-approx/t-digest-approx, TZ CAST fetch, odd-identifier quoting). 350/350 pytest green, tsc clean 2026-10-24.
- [x] **M2-T4** Profilers: statistical + missingness (+ reference tests). *Spec: ENGINE §3; TESTING §3.1.* ✅ `engine/metrics/profiler.py` — `execute_stat_and_missingness(con, handle, report, plan, config, registry)` consumes the M2-T3 `ExecutionPlan`, renders each templated stats/missingness decision via `render_sql` against the DuckDB view (or ONE shared bounded sample relation for `sampled` decisions — seeded md5 ORDER+LIMIT, reservoir finalised in M2-T10), stamps scalars into `ColumnProfile.stats`/`.missingness` as `MetricValue`+`Provenance(**decision.provenance_kwargs())`. Honesty enforced: not_computed ⇒ value key ABSENT (`model_construct`+`fields_set.discard`); approximate-without-approx-template (stddev/quick) falls back to exact SQL but keeps the `approximate` label; §6.4 per-metric `duckdb.Error`→`RunErrorEntry(METRIC_NOT_COMPUTED)`+not_computed, run continues; DECIMAL→float JSON-safe. **ADR-017** (new): sentinel set is dataset-config input `profiling.missing.sentinel_values` (exact/case-sensitive, bound via `executemany($1)` into temp `dpe_sentinels`, never interpolated; absent ⇒ `sentinel_count` 0) — added to `dataset_config.schema.json`+`ConfigMissing` pydantic+`contracts.ts`, CONTRACTS §9 exemplar, ENGINE §3 note; + `missingness.{sentinel_count,missing_rate}` registry templates + `render_sql(params=…)` w/ unsubstituted-placeholder guard. +`tests/test_profilers_stats_missingness.py` (24 tests: stats vs Polars `std(ddof=1)`/`quantile(linear)` + hand-computed; missingness null/blank/sentinel/missing_rate+denominator; ADR-017 absent/case-sensitive/bound-quote; exact/approximate(t-digest)/sampled(seed+determinism+bounded)/not_computed(reason,no value); §6.4 partial-failure; empty relation; DECIMAL transport; §3.2 schema-valid + byte-stable rerun) + 1 new render_sql placeholder-guard test. 375/375 pytest green, tsc clean 2026-10-24.
- [x] **M2-T5** Profilers: uniqueness + categorical (+ reference tests; HLL provenance). *Spec: ENGINE §3, §2.2.* ✅ `engine/metrics/profiler.py` — `execute_uniqueness_and_categorical(con, handle, report, plan, config, registry)` runs the M2-T5 families. **Uniqueness**: `distinct_count` renders the templated exact OR HLL approx SQL by the planner's method label (algorithm=HLL stamped via `decision.provenance_kwargs()`); `is_unique_candidate` derived from `distinct_count.value == count(col)` on the SAME scanned relation (bool has no provenance carrier, honesty rides on the paired MetricValue); `duplicate_groups` (contract_target=None) built inline → auxiliary `ProfilerResult.duplicate_group_counts` for M2-T8. **Categorical**: `frequencies`/`rare_categories`/`label_inconsistencies` all built inline (registry has no templates); §4 low-cardinality gate reads the just-computed `uniqueness.distinct_count` (`CATEGORICAL_MAX_DISTINCT=1_000` → block ABOVE that threshold stays ABSENT, never a truncated top-K masquerading as full enumeration); `CATEGORICAL_TOP_K=50`, rare = count ≤ `max(1, floor(non_null * 0.001))`, label_inconsistencies = same `lower(trim(x))` with ≥2 raw variants. Approximate decisions without an approx template (`is_unique_candidate` at STANDARD-large, `label_inconsistencies`) fall back to EXACT SQL, planner's approximate label retained — same M2-T3 delegation M2-T4 established for stddev/quick. §6.4 per-metric `duckdb.Error` → `RunErrorEntry(METRIC_NOT_COMPUTED)` + honest absence (not_computed `MetricValue` for distinct_count; boolean default False; unset categorical keys DROPPED — pydantic strict rejects an explicit null on non-nullable, keys are omitted not nulled — gotcha M2-T5). Section gating: when §4 gate 2 disables the `uniqueness` section, the whole `ColumnProfile.uniqueness` block is structurally OMITTED (tracked via `_UniCatSlot.uniqueness_executed`), never fabricated. + `tests/test_profilers_uniqueness_categorical.py` (27 tests: distinct_count vs Polars `n_unique` + hand, `is_unique_candidate` identifier/duplicate/all-null-vacuously-True, duplicate_group_counts parity, HLL approximate path (n_rows > LARGE_ROW_LIMIT labelled `algorithm=HLL`, within documented error bounds), sampled path (seed + bounded + deterministic), top-K frequency desc/raw-asc + Polars group_by parity + NULL exclusion + numeric-column registry gate, rare threshold + empty no-singleton, label_inconsistencies case+whitespace collapse (DuckDB VARCHAR sort is byte-ASCII: `ACTIVE`<`Active`<`active`), high-cardinality >1_000 gate omits the whole categorical block, §4 gate-2 uniqueness section-off omits the whole uniqueness block, §6.4 partial failure, empty-relation edges, every profile §3.2 schema-valid across STANDARD/QUICK/DEEP + byte-stable rerun). 402/402 pytest green, tsc clean 2026-10-24.
- [x] **M2-T6** Profilers: distribution (histogram, outliers) + temporal single-column (+ reference tests). *Spec: ENGINE §3, §2.2; TESTING §3.1.* ✅ `engine/metrics/profiler.py` — `execute_distribution_and_temporal(con, handle, report, plan, config, registry)` runs the M2-T6 families. **Distribution**: histogram BUILT inline via a width_bucket EMULATION (`CAST(LEAST(nb, FLOOR((col-lo)/(hi-lo)*nb)+1) AS INTEGER)` — DuckDB 2.0 has no `width_bucket` scalar), 10 fixed bins over [min,max], counts aligned to bins, top-edge folds into last bin, constant→single bin, empty→honest not_computed ([] bins/counts), bin_strategy/edges in `method_params`; `skewness` renders the templated `skewness(col)` (DuckDB = adjusted Fisher–Pearson **G1**, distinct from Polars' biased g1; undefined→not_computed+reason, value ABSENT); `outlier_candidates` built via Tukey IQR/1.5 fences from one `quantile_cont` q1/q3 summary pass → `OutlierCandidates{count, method='iqr_1_5', provenance, sample_row_refs=['row:N']}` (row *references*, never raw values; omitted at count==0). **Temporal**: range via render_sql (CAST AS VARCHAR, TZ-safe), `volume_per_granularity` (date_trunc + auto granularity from span, bounded), `gaps` (expected-minus-observed `generate_series` INTERVAL), `out_of_expected_range` honest not_computed (no MVP config bound), `timezone_source` naive/utc; nested Provenance in an open dict MUST be `.model_dump(mode='json')`. Section gating: disabled `distributions`/`temporal` ⇒ whole block structurally OMITTED (dist_active/temporal_active), never zeroed; approximate-without-approx-template (quick histogram/skewness) falls back to exact SQL keeping the label; §6.4 per-metric `duckdb.Error`→`RunErrorEntry(METRIC_NOT_COMPUTED)`. + `tests/test_profilers_distribution_temporal.py` (28 tests: histogram bin-count + non-null sum invariant vs Polars, edges/strategy, top-edge/constant/empty; skewness==G1 reference + differs-from-Polars-g1 + constant not_computed; outlier IQR count vs hand Tukey + refs-are-row:N + no-outlier omit + seeded count=2; temporal range/month-volume/gaps/day-gaps/out-of-range/naive-vs-utc-tz; distributions-off & temporal-off gating; quick approximate label+algorithm; sampled gaps seed+bounded+deterministic; §6.4 broken-skewness isolation; every profile §3.2 schema-valid STANDARD/QUICK/DEEP + byte-stable rerun + numeric-has-no-temporal-vice-versa). 430/430 pytest green, tsc clean 2026-10-24.
- [x] **M2-T7** Rule registry + compiler → one SQL per rule; statuses PASS/FAIL/WARN/ERROR. *Spec: ENGINE §5.1–5.3; CONTRACTS §4.1.* ✅ `quality/` package — `registry.py` (ENGINE §5.1 closed catalog `RuleDefinition`{measure∈count|rate|outlier, param_kind, warn_only, mvp_active}; `referential_integrity`/`date_order` mvp_active=False → ERROR), `compiler.py` (`compile_rule` → ONE DuckDB statement shaped `(violations,total,sample_row_ids,lower_bound,upper_bound)`; mirrors `render_sql` single-materialisation + ADR-017 **bound input** (every threshold/domain/condition value is a `$n` placeholder, only identifiers quoted inline); target + condition columns validated present else `RuleCompileError`→ERROR; §5.2 missing = `null OR trim=''` (sentinels deliberately excluded — ColumnProfile concern); unique = `nonnull−distinct` (per-group, not a window `SUM(grp_n-1)`); outlier_scan reuses Tukey IQR/1.5 `quantile_cont` fences into `threshold{between}`; `sample_row_ids` bounded `array_agg … ORDER BY rid LIMIT≤200` → `row:N` refs never raw values; CTE SELECT has no outer FROM so empty ⇒ one row), `executor.py` (`run_quality_checks(con,handle,config,run_id,discovery?,executed_at?)`→`QualityRunResult{checks,errors}`; status PASS(0)/FAIL(>0)/WARN(warn_only|business_note|severity==warning)/ERROR(cannot compile|execute→`RunErrorEntry(RULE_EXEC_FAILED)`+observed null, **FAIL≠ERROR**); non-error `observed=MetricValue{count|rate, exact, denominator=population}`; `schema_version`='1.0', `rule_version`='1'). +`tests/test_quality_rules.py` (29: every §5.1 MVP type vs hand verdict + type_valid direct-INTEGER FAIL; outlier WARN-not-FAIL w/ fences; business_note/warning→WARN; absent-col/Phase-2/broken-quantile/malformed-range→ERROR w/ null observed; §6.4 isolation; bound `it''s` literal; `row:N` refs + ≤200 cap on 300 rows; every result §4.1-schema-valid). **No contract change** (consumes frozen §9 rules + emits frozen §4.1 results). 459/459 pytest green, tsc clean 2026-10-24.
- [x] **M2-T8** Findings: detectors, dedup/merge, prioritization, **fingerprint**. *Spec: ENGINE §6; CONTRACTS §4.4.* ✅ `findings/` package — `fingerprinting.py` (ADR-016 §4.4 `sha256(category\|scope_key\|detector_id\|normalized_measure)`, 0x1F/0x1E collision-safe join, scope_key=dataset_id+table?+column, identity rides rule/metric id NOT value → same problem/different numbers = same digest), `detectors.py` (§6.1 two sources: `detect_from_quality_checks` FAIL/WARN→finding w/ rule observed Provenance + [metric,rule] refs — **PASS & ERROR both emit nothing**, FAIL≠ERROR, confidence↔method exact>approx>sampled; `detect_from_profiles` rule-less extremes as WARN: near-total missingness ≥0.90, constant column distinct==1&denom≥2→UNIQUENESS, dominant category share≥0.95→ACCURACY_FLAGGED, duplicate-key auxiliary from `ProfilerResult.duplicate_group_counts` (M2-T5's contract_target=None signal); facts/interpretation/hypotheses structurally separate §7, hypotheses MVP=unverified, never invents a number), `pipeline.py` (dedup collapses same (column,category) keeping top-priority rep + unions evidence; `priority_of` = deterministic int f(severity, critical-PK, affected-share, confidence) — **ordering only, no priority field added to frozen §4.2**; sort (-priority,fingerprint)→F-001.., workflow=null). `__init__.py` `build_findings(...)` critical scope from config PK. **No contract change** (reuses frozen Finding + finding.schema.json, consumes only M2-T4..T7 evidence). +`tests/test_findings.py` (29: fingerprint stability/distinction, FAIL/WARN→find·PASS/ERROR→none, each detector past/silent at bound, dedup merge, priority determinism, every finding §4.2-schema-valid + MVP invariants, quality→findings integration). 488/488 pytest green, tsc clean 2026-10-24.
- [x] **M2-T9** Recommendations: rule-based templates + narrative w/ evidence refs. *Spec: ENGINE §7; CONTRACTS §4.3.* ✅ `recommendations/` package — `templates.py` (ENGINE §7 closed catalog: `RecommendationTemplate`{kind,reason_type,action,rationale,target_improvement,do_not,probe} per problem-kind; 6 rule kinds keyed by `finding.category` + 4 profile-extreme kinds (missingness/constant/duplicate-key/dominant-category) recovered from the representative fact `metric_ref` suffix + 6 defensive generics = 16; `classify_finding_kind(Finding)` re-derives detector identity (frozen §4.2 has no `detector_id`): any `rule:` ref ⇒ rule kind by category, else `facts[0].metric_ref` suffix ⇒ profile kind; accuracy_flagged rule OR dominant-category profile ⇒ `business_context_required` investigation question, never a proven cause — ENGINE §5 "accuracy is never inferred from validity"), `narrative.py` (composes text substituting ONLY `{column}` — numbers stay in the cited evidence refs, never re-typed; probe folded into action; rationale quotes refs verbatim), `rule_based.py` (`recommendation_for_finding` copies `finding.evidence_refs` **verbatim** onto the `Recommendation`, `evidence`⇒refs required, `status=proposed`, `R-NNN`, `finding_id` back-ref), `__init__.py` (`build_recommendations(findings)`→`RecommendationsResult{recommendations,findings}`: one rec per finding (≥1 mandate), deterministic `R-NNN` in F-NNN order; **wires `Finding.recommendation_ids`** M2-T8 left `[]` via a `model_dump`→set→`model_validate` round-trip so the strict ContractModel emits the key + re-validates §4.2). **No contract change** (reuses frozen §4.3 `Recommendation`+`recommendation.schema.json` M1-T2 + `finding.schema.json` `recommendation_ids`). +`tests/test_recommendations.py` (24: each detector→kind incl. all 4 profile extremes + rule-by-category + every kind resolves; evidence⇒non-empty + verbatim subset + rationale quotes refs; dominant-category/accuracy-rule→business w/ investigation-verb action; column substituted but value NOT reproduced; every rec §4.3-schema-valid + `R-NNN` + back-ref + proposed + ≥1/finding; wiring before/after + wired finding §4.2-valid + fields preserved; byte-stable; zero-fill guardrail; all 6 rule categories covered). AI/LLM OUT (BLUEPRINT Q3). 512/512 pytest green, tsc clean 2026-10-24.
- [x] **M2-T10** Modes quick/standard/deep + sampling (reservoir, seed) wiring. *Spec: ENGINE §2.2, §8.* ✅ Two deliverables. **(1) Reservoir finalised** in `engine/metrics/profiler.py::_sample_relation` — the preliminary `md5('seed#‖_row_id') ORDER+LIMIT` is replaced by a **real seeded reservoir**: DuckDB `USING SAMPLE RESERVOIR (size ROWS) REPEATABLE (seed)` (uniform over the whole relation, returns-all when n<size, reproducible per seed, different seed ⇒ different subset); `block` keeps locality via a bounded `LIMIT` scan (ENGINE §2.2). `REPEATABLE` binds the planner-verified seed as an int literal, never user data (ADR-017). **Gotcha:** the pre-existing defensive `DROP VIEW IF EXISTS` collided with the `TEMP TABLE` a prior pass created under the same content-hashed window name → switched to `DROP TABLE IF EXISTS` (the pipeline builds the same window across the 3 passes). **(2) Engine path wired end-to-end** in `orchestration/__init__.py` — `run_profiling_pipeline(con, handle, config, run_id)` reads `config.profiling.{mode,sampling,sections}` (re-hydrating the `use_enum_values` str `mode`→`ProfileMode`), runs discover→plan_execution(mode, n_rows=FULL count, sampling, sections)→the three M2-T4/T5/T6 passes against ONE shared plan+reservoir→`_merge_profiles` composes the family-disjoint blocks (stats/missingness | uniqueness/categorical | distribution/temporal) onto each discovery-skeleton ColumnProfile via `model_copy(update=)`→run_quality_checks (full relation)→build_findings→build_recommendations, returning `ProfilingPipelineResult{mode,n_rows,columns,checks,findings(recommendation_ids wired),recommendations,errors(§6.4 union),sampling(RunSampling record: §9 `size`→§2 `sample_size`+seed, None when disabled),plan,discovery,handle,duplicate_group_counts}`. `open_source` + `run_dataset_profiling(dataset_id, path, config, run_id)` open CSV/Parquet (ADR-011) then run. **A sampled run stamps `method=sampled`+`method_params.seed` on EVERY sampled metric end-to-end (ADR-015)** — proven by a test that walks the plan + emitted provenance. **No contract change; NO new metric; §3.1 DatasetProfile / §2 ProfilingRun rollup assembly (row_count/PK-candidates/temporal_coverage/timings) deliberately DEFERRED to M2-T11** (user-confirmed scope split; runner exposes columns + sampling record + plan for the CLI to fold). +`tests/test_orchestration_modes_sampling.py` (19: reservoir bounded/deterministic-same-seed/differs-diff-seed/returns-all/block-scan/reuse-name; standard-exact vs quick-approximate(+algorithm) vs deep-exact on the expensive metric; seed stamped on every sampled decision AND every sampled MetricValue; RunSampling populated/None; sampled run byte-stable; all-families merge onto one profile (numeric stats+distribution, varchar categorical); section-off omits blocks; findings↔recommendations wired + recommendation_ids non-empty; every column §3.2-schema-valid across quick/standard/deep; quality rule verdict unaffected by a sampled window; CSV end-to-end via run_dataset_profiling). 531/531 pytest green, tsc clean 2026-10-24.
- [x] **M2-T11** `profiler-cli` end-to-end on `sales_deals` fixture → full contract-valid JSON. **→ GATE M2 (BLUEPRINT Phase 1).** *Spec: CONTRACTS §2–4; TESTING §2.* ✅ Closed the contract rollup M2-T10 deferred. `orchestration/rollup.py` executes the dataset-scope plan decisions (`schema.row_count`/`column_count`/`key_candidates` — no profiler touched them) and folds the pipeline result + scalars into the frozen §3.1 `DatasetProfile` (row_count MetricValue + column_count + primary_key_candidates + temporal_coverage + columns) and §2 `ProfilingRun` (dataset/source_snapshot from handle, config{config_version, config_hash=sha256(model_dump_json), profile_mode}, engine, timing, sampling=M2-T10 RunSampling, terminal status, error_log=result.errors); `profile_dataset()` is the single spine entry (M5 worker target). `reporting/__init__.py` deterministic JSON writer (`result_document` + `write_profile_json`, sort_keys byte-stable). `cli/__init__.py` argparse `profiler-cli profile --input --config --mode --out --dataset-id --run-id --allow-root`; `--config` loads the §9 YAML authoring form via `yaml.safe_load` (JSON ⊂ YAML; BLUEPRINT Phase 1 `--config x.yaml`; ADR-012) and rejects invalid config BEFORE compute (AGENTS §5). Honesty carried: row_count/column_count stay `exact` under sampling (full-view recompute); key_candidates over FULL relation (`requires_full_relation`), omitted when not_computed (never fabricated); §6.4 FAIL≠ERROR at run level; sampled REQUIRES seed (ADR-015); not_computed reason + no value. NO new metric; no HTTP/server (ADR-005); packaged entry stays `main.py`. Gotchas: `ContractModel` rejects explicit null on non-nullable optional → `_instantiate()` omits None keys not in `NULLABLE_FIELDS`; +`pyyaml>=6.0` (uv-managed venv). +`tests/test_cli_rollup.py` (20: dataset metrics, full §2/§3.1/§3.2/check/finding/rec schema-validity, reproducibility tuple, FAIL≠ERROR, sampling record absent/present + row_count-exact-under-sampling, byte-deterministic writer, CLI end-to-end CSV+parquet+YAML+mode-override+invalid-config-rejection). 551/551 pytest green, tsc clean 2026-10-24. **M2 spine done; full Phase-1 gate still needs M3 (SQLite) + M4 (HTML/Plotly/CSV) + 1M-row scale benchmark.**

## M3 — Persistence

- [x] **M3-T1** SQLite metadata schema (runs/jobs/registry/checks/findings refs) + migrations. *Spec: ADR-008; SECURITY §2, §6.5.* ✅ New `storage/` package: `paths.py` (OS-standard user-data/cache/temp resolution + `DPE_DATA_DIR`/`DPE_CACHE_DIR` overrides + app-bundle containment guard + 0o700 private dirs — SECURITY §2/§6.5), `migrations.py` (gapless forward-only `Migration` chain at `TARGET_VERSION=1`, `schema_migrations` + `PRAGMA user_version`, pre-migration online-backup `metadata.pre-migration-v{n}-{ts}Z.sqlite3`, downgrade + unversioned-db refusal, one atomic `BEGIN IMMEDIATE` per migration), `metadata_store.py` (`MetadataStore.save_run()` persists an M2-T11 `ProfileBundle` — §2 run, §3.1 profile, column inventory, §4.1 checks, §4.2 findings (ADR-016 fingerprint indexed, not unique), §4.3 recommendations, `error_log`/warnings, artifact references — in ONE transaction; full typed readers rebuild schema-valid objects identical to the input; registry/config-version/job APIs ready for M5; run-history + fingerprint queries ready for M6). WAL + `foreign_keys=ON` + `busy_timeout` + `isolation_level=None` deliver ADR-008 concurrent-read-during-run and crash-atomicity. 19 v1 tables, blobs stay in the artifact store (referenced by `run_artifacts`). NEW §8 code `METADATA_WRITE_FAILED`. NO artifact file lifecycle (M3-T2), NO normalized metric rows (M3-T3), no contract change, no new metric. +`tests/test_storage_metadata.py` (55). 607/607 pytest green, tsc clean 2026-10-24.
- [x] **M3-T2** Artifact store: layout, lifecycle policy, cleanup. *Spec: SECURITY §2.* ✅ `storage/artifact_store.py` (store-relative `artifacts/<run_id>/<file>` layout, `resolve()` confinement → SOURCE_NOT_ALLOWED, deterministic-JSON `write_document`, `RetentionPolicy`/`KIND_RETENTION`, `delete_run_artifacts`/`delete_dataset_artifacts`/`sweep_orphans`/`clear_staging`/`clear_cache`, staging `stage()` contextmanager; all IO wrapped in `ArtifactStoreError`). +`tests/test_storage_artifact_store.py` (26). See session note above for full detail.
- [x] **M3-T3** Normalized metric persistence `(run_id, column, metric)` for cheap diffs. *Spec: ADR-008; ENGINE §9.* ✅ Forward migration v2 `column_metric_values` (keyed `(run_id, column_name, metric_name)`, one row per §3.2 `MetricValue` leaf, ADR-015 `method`+`method_params`(seed)+`reason`+`denominator`, absent-vs-null `value_json`, FK cascade, two diff indexes; v1 DDL frozen). `MetadataStore._insert_column_metrics` (in the save_run tx) + reads `column_metrics`/`metric_value`/`metric_history`; `_iter_column_metrics` enumerates the promoted leaves. Artifact FILE stays authoritative (rows are the cheap-diff index). +`tests/test_storage_metadata.py` (55→64) + v1→v2 migration-test bumps. See session note above. **→ closes GATE M3 (M3 complete).**

## M4 — Reporting Layer

- [x] **M4-T1** JSON result writer from contracts (golden snapshots). *Spec: ENGINE §10; TESTING §2.* ✅ FORMAL golden-snapshot harness over the M2-T11 writer (`reporting.write_profile_json`/`result_document`) — NO new writer, no contract shape change. New corpus dir `tests/fixtures/golden/` (checked-in triple: `sales_deals.csv` bytes, `sales_deals.config.json`, `sales_deals.golden.json` — 27 KB normalized). New `tests/golden_harness.py`: `normalize_document()` redacts ONLY the machine-dependent pointers (source_ref, source_snapshot.path/modified_at, engine.duckdb_version/python_version, timing.started_at/finished_at/duration_ms→0, per-check executed_at, result_refs) to stable sentinel tokens (`<VOLATILE>`, `REDACTED_INT=0` where the schema types the slot integer); the reproducibility tuple (content_hash + config_hash + engine_version + metrics + fingerprints + statuses + provenance.method) is preserved verbatim so drift is loud. `emit_result_document()` runs `profile_dataset` on the checked-in fixture and reads back the WRITTEN bytes (exercises the on-file writer, not only the in-memory dict). Regeneration path via `DPE_UPDATE_GOLDEN=1` (never set in CI). New `tests/test_reporting_golden.py` (8): byte-diff of normalized JSON against the golden (with a compact first-mismatch context on failure); every block schema-validates against its JSON Schema from disk; volatile fields ARE redacted (a future rollup change that starts leaking a machine path or wall-clock stamp into the golden fails loudly) and the reproducibility tuple is NOT; two-emit stability after normalization (TESTING §3.3); the golden carries the injected FAILs (`deal_id_unique`, `value_not_null`) + `stage_domain` PASS + fingerprints + recommendation back-refs; `config_hash(load_config(name))` matches the golden (config↔golden coupling, no drift between the two files); fixture-triple presence; harness self-check (deep copy). New `.gitattributes` freezes the golden dir with `-text` so CRLF autocrlf on Windows cannot shift `content_hash`. 650/650 pytest green, tsc clean 2026-10-24.
- [x] **M4-T2** HTML self-contained report, inline Plotly, method badges, masking default. *Spec: ENGINE §10; SECURITY §4.* ✅ `reporting/html_report.py` renders the M4-T1 `result_document(bundle)` only (no recomputation); vendored `assets/plotly.min.js` (plotly 7.1.0, sha256-pinned test) inlined — zero external tags; every MetricValue wears its ADR-015 badge (seed/algorithm/reason in tooltip); PII := discovery `logical_type` ∈ {email, phone} (NOT semantic_type — the old NEXT memo was wrong), values masked by default in tables+charts; §10 chart vocabulary all emitted (histogram, categorical bar, missingness bar, outlier box, temporal volume); findings show ADR-016 fingerprint + working evidence back-links; `emit_html_report_artifact` → canonical `html_report` kind (`artifacts/<run>/report.html`), CLI `--html`, spec-file `datas` bumped. +25 tests, browser-verified offline. See session note above. **→ NEXT: M4-T3.**
- [x] **M4-T3** CSV issue export columns + artifact refs. *Spec: ENGINE §10; SECURITY §4.* ✅ New `reporting/csv_export.py` (render/write/emit) consumes the SAME `ProfileBundle` via `result_document`-free typed objects, zero recomputation. Header verbatim from §10: `finding_id, rule_id, column, status, severity, observed, threshold, affected_records, evidence_ref`. Row source = findings joined to their originating **FAIL/WARN** check via the finding's `rule:<rule_id>` evidence ref (detectors only make rule findings from FAIL/WARN, so PASS/ERROR never appear — FAIL≠ERROR honored); a profile-extreme finding has no `rule:` ref ⇒ no row; a merged finding carrying two rules ⇒ two rows sharing `finding_id`. `observed` cell always names its ADR-015 `method` (+ seed/algorithm for sampled/approximate; `not_computed`→reason, never a blank-as-zero). SECURITY §4 satisfied by construction: an issue row carries only rule ids, column *names*, aggregate metrics and config thresholds — no raw source value is ever emitted (test asserts an email column leaks no value). Deterministic byte output (`csv` `lineterminator=\n`, `QUOTE_MINIMAL` quotes comma-bearing thresholds). `emit_issue_csv_artifact` → canonical `issue_csv` kind (`artifacts/<run>/issues.csv`, already in `DEFAULT_FILENAMES`/`KIND_RETENTION`=RESULT/CONTRACTS §2 result_refs). CLI `+--csv OUT` on `profile` (JSON/HTML paths untouched). +19 tests `tests/test_reporting_csv.py` (header verbatim, golden e2e = exactly the 2 FAIL rules, WARN-in/PASS-out/ERROR-out, merged→2 rows, sampled seed, not_computed reason, between `0..100`, dataset-scope column fallback + affected_records from finding scope, comma-quoting, no-PII-leak, writer parents, ArtifactStore canonical ref, CLI flag). 694/694 pytest green. **→ NEXT: M4-T4.**
- [x] **M4-T4** Language decision (BLUEPRINT Q1) recorded here, then templates. *Spec: BLUEPRINT §8.* ✅ Q1 ratified → ADR-018; bilingual EN+ID recommendation templates shipped, `DatasetConfig.language` opt-in (default "en", omitted-when-unset keeps golden/config_hash byte-identical). See session note in CURRENT POSITION. 706/706 pytest green, tsc clean 2026-10-24. **→ Closes GATE M4 (dev-mode; packaged verification in M7).**

## M5 — Bridge Facade + Job Manager (full)

- [x] **M5-T1** Command handlers for MVP command set; payload caps enforced. *Spec: CONTRACTS §5.2, §5.4.* ✅ All nine §5.2 commands wired. `bridge/envelope.py`: 7 new payload models (all `extra="forbid"`) — `DatasetInspectPayload`, `DatasetPreviewPayload` (`limit ≤ PREVIEW_ROW_CAP=200`, `mask_pii=True`), `PagedResultPayload` (requires `run_id` **or** `dataset_id`, `limit` 1..200, `offset ≥ 0`), `QualityGetResultsPayload` (`status`/`dimension`), `FindingsGetPayload` (`severity`/`category`), `ReportSummaryPayload`, `ArtifactOpenPayload` (`kind` XOR `ref`, `reveal=False`). `bridge/facade.py`: 6 handlers served from `runtime.get_metadata_store()` / the M2 connector seam — `dataset.inspect` (connector `build_snapshot` + `LIMIT 0` `_probe_schema`, joins stored `column_inventory` types + `profile_summary.row_count` + `last_run`; **never reads rows**, ADR-007), `dataset.preview` (DuckDB `LIMIT/OFFSET` page only, `_row_id` plumbing stripped, PII masked by stored `logical_type` column tag or a cell matching `discovery.inference.PII_PATTERNS`, `mask_pii=False` opt-out), `quality.get_results` / `findings.get` (paged ids from `list_checks`/`list_findings`, items rebuilt as contract §4.1/§4.2 objects via `load_check_results`/`load_findings`, `page.total` from new `count_checks`/`count_findings`), `report.get_summary` (`load_profiling_run` + `profile_summary` + `counts` + `check_status_counts` — counts/refs only, no big payload), `artifact.open` (ref resolvable ONLY through a recorded `run_artifacts` row → `ArtifactStore.open_path` confinement; returns an ack `{run_id,kind,ref,size_bytes}`, never content; `reveal` uses the OS native viewer, best-effort). Caps: request envelope cap + **new response cap** in `bridge_send` (`BRIDGE_PAYLOAD_TOO_LARGE` when the serialised result >1 MiB); handler filter vocabularies validated so a typo'd filter is typed `BRIDGE_REQUEST_INVALID`, not an empty page. `_resolve_source_path` applies the SECURITY §3 allow-list (`SOURCE_NOT_ALLOWED`); `_connector_error_result` maps connector codes onto §8; `_json_safe` coerces Decimal/date/bytes; `_error()` now derives `retryable` from `errors.is_retryable(code)` (§8) instead of hard-coding False. No new metric, no HTTP (ADR-005), no new §8 code, no architecture change. `contracts.ts` mirrors the payloads + result shapes (no new `<Name>Values` enum, so the 18-enum parity test still passes). +`tests/test_bridge_read_commands.py` (38, seeded by a REAL in-process `profile_dataset` run + ArtifactStore writes + `save_run`, `configure_runtime(root)` redirect). 753/753 pytest green, tsc clean 2026-10-25. Gotchas logged in GOTCHAS. **→ NEXT: M5-T3.**
- [x] **M5-T2** Job manager: lifecycle, cancel, timeout, crash detection, recovery. *Spec: CONTRACTS §6; ADR-006.* ✅ Real write path live. `bridge/worker/profile_job.py::run_profile_job` = spawn-safe worker entry (all-positional primitive args, ADR-006): runs the FULL M2-M4 spine via `orchestration.rollup.profile_dataset` in the worker, emits + persists the run (ArtifactStore + `MetadataStore.save_run` on a worker-thread store), reports `ProgressEvent`/`TerminalEvent` over the IPC queue, honors cancel at step boundaries. `manager.py`: `_launch` generalized (echo spike + real job share one lifecycle), `start_profile_job` (persist=True, `PROFILE_JOB_TIMEOUT_SECONDS=1800`, QUEUED row mirrored to SQLite so `runs.job_id` resolves — `register_dataset`→`create_job` ordering FK-safe), `_persist` feeder-thread-safe (short-lived per-call store, canonical §5.3 `_canonical_status` conversion before write), `recover_interrupted_jobs` (orphaned open rows ⇒ honest FAILED/WORKER_CRASHED, §6.3) invoked once when `runtime.get_profile_manager()` first materializes. `bridge/runtime.py` = process-wide context singleton (`configure_runtime` seam redirects data root + rebuilds store/manager; tests use tmp_path, packaged app uses OS default). `envelope.py`: `ProfileStartPayload` (validated before spawn; invalid config rejected at the boundary, AGENTS §5). `facade.py`: `profile.start` / `profile.get_status` / `profile.cancel` handlers; spike commands behind `enable_spike_commands` (default env `DPE_ENABLE_SPIKE_COMMANDS`, OFF in packaged build). `rollup.profile_dataset` gained optional `job_id` threaded into `build_profiling_run` so the run row links to the job — CLI/golden path leaves it None (field absent per contract, golden byte-identical, verified). NO HTTP (ADR-005), no architecture change; status payloads are canonical §5.3 `JobStatus` ≤ bridge caps. +`tests/test_bridge_profile.py` (9: e2e profile.start→SUCCEEDED with schema-valid persisted run + artifacts on disk, invalid-config rejection, cancel → CANCELLED, unknown job, spike-off-by-default, recovery, payload validation, status canonical shape, timeout path). Spike fixtures updated to pass the enable flag. 715/715 pytest green, tsc clean 2026-10-25. Gotchas logged in GOTCHAS. **→ NEXT: M5-T1.**
- [x] **M5-T3** Progress polling + optional evaluate_js push, one status schema. *Spec: CONTRACTS §5.5.* ✅ ONE serialiser feeds both transports: `worker/manager.py::canonical_status_payload(status)` = `_canonical_status(...).model_dump(mode="json", exclude_none=True)` — the facade's poll (`_status_payload`) and every push come out of it, so `job_id` is back INSIDE the polled object (§5.3 sample carries it; a push has no envelope). Manager observers: `add_status_listener` (idempotent, lazily starts ONE daemon `job-status-push` thread), `_notify` (coalesces newest-status-per-`job_id`, snapshot only, under the lock), `_dispatch_loop` (delivers outside the lock, swallows listener faults); all 7 transition sites now publish via the new `_commit(record)` = `_persist` (SQLite mirror §6.3) + `_notify`, so a push can never alter or fail a job. `runtime.py`: `configure_status_push(sink)` / `status_push_enabled()` / `window_push_sink(window)` (JSON-dumps the status, DROPS >1 MiB instead of truncating §5.4, emits the fixed `window.__dpeJobStatus && __dpeJobStatus(<json>);` — only our own object interpolated, SECURITY §3; WebView errors swallowed) and `get_profile_manager()` attaches `_forward_status` to every manager it hands out, so a host that installs no sink stays inert. `main.py` installs the sink right after `webview.create_window`. `envelope.py` now OWNS `PAYLOAD_CAP_BYTES` (moved out of the facade) so request, response and push gate on one number. Contracts: `contracts.ts` mirrors `job_status.schema.json` (`JobStateValues` + `JobProgress` + `JobStatus`), parity `ROOT_INTERFACES` gained `job_status.schema.json → JobStatus` and the enum-set test is now **19**; `docs/CONTRACTS.md` §5.5 gained the binding push details (callback name, coalescing/drop rules, poll authoritative) — no new command, no new §8 code, no HTTP, no per-transport schema fork (ADR-005/§5.5). +`tests/test_bridge_progress_push.py` (11: pushed object == polled object and both schema-clean, absence fidelity + monotonic progress, burst collapses to newest, listener-free manager queues nothing, exploding sink cannot fail a job, no-sink inert, `configure_runtime` keeps the host sink, one forwarder per fresh manager, sink expression JSON round-trip, oversized push dropped, dead window + unserialisable status swallowed). 765/765 pytest green, tsc clean 2026-10-25. Gotchas logged. **→ NEXT: M6-T1.**

## M6 — UI Screens (one task = one screen)

- [x] **M6-T1** Dataset Selection + inspect/preview. *Spec: BLUEPRINT Phase 2; CONTRACTS §5.* ✅ First real UI screen. `bridge/api.ts` (typed §5.3 envelope client: `BridgeRequest`/`BridgeResponse`/`BridgeError`; `bridgeSend<R>` unwraps ok→result; `bridgeStartJob` returns job_id; `onPushedJobStatus` registers the optional §5.5 `__dpeJobStatus` push sink; `bridgeAvailable` detects shell). `DatasetSelection.tsx`: path text input → `dataset.inspect` metadata grid (file, connector, size, content_hash, columns table w/ logical+semantic types when stored run exists, last_run badge, row_count + method tag) → auto `dataset.preview` (50-row paged, PII mask toggle, lock icons, masking_applied info, prev/next). `App.tsx`: route `/` → DatasetSelection. `styles.css`: +150 lines (path-row, meta-grid, inspect/preview-panel, table-scroll, pager, error-box, banner-warn, mask-toggle, last-run-badge, dark-mode variants). No Python changes, no new §5.2 commands, no new enum, 765 tests green, tsc clean, vite build 5.6s. **→ NEXT: M6-T2.**
- [x] **M6-T2** Run Configuration (mode, sampling, config upload w/ validation errors). *Spec: CONTRACTS §9.* ✅ `RunConfig.tsx` + `config/validation.ts` (client §9 mirror — js-yaml parse, schema-mirrored validator, form→document builder) + `state/selection.ts` (cross-screen dataset/job stores). Mode always sent as the §5.3 `profile_mode` override (worker folds it in, so validated==run); sampling enabled ⇒ strategy+size+seed enforced (ADR-015); uploaded invalid doc blocks Start locally AND server `CONFIG_INVALID` stays authority (no worker spawns on bad config); §9 exemplar validated 0-error on both sides. New dep js-yaml. No Python/contract change; 765/765 pytest green, tsc clean, vite build clean 2026-10-25. **→ NEXT: M6-T3.**
- [x] **M6-T3** Run Progress (status/percent/cancel). *Spec: CONTRACTS §6.* ✅ `RunProgress.tsx` (/progress): polls `profile.get_status` @700ms (authoritative §5.5) + merges `onPushedJobStatus` hints through one state-rank gate (QUEUED<RUNNING<CANCEL_REQUESTED<terminal — a late message never regresses the bar; the only legal backwards move is a polled RUNNING after CANCEL_REQUESTED, §6.5); §6.2 badge + progress bar (completed/total_steps, current_step); Cancel → `profile.cancel`, locks on optimistic CANCEL_REQUESTED until a polled terminal confirms; JOB_NOT_FOUND (not-retryable §8) stops the poll with an explicit message; SUCCEEDED renders the bounded `_bounded_result` summary (run_id, counts, artifact ref names). `App.tsx` +route/nav, `RunConfig.tsx` banner links to /progress, `styles.css` +62. No Python/contract change; 765/765 pytest green, tsc clean, vite build clean 2026-10-25. **→ NEXT: M6-T4.**
- [x] **M6-T4** Report View: Overview + Column Profiling. *Spec: expected-output §3.1–3.2.* ✅ Added §5.2 `report.get_columns` read command (ADR-019 — the sanctioned contract change; see session note). Overview via `report.get_summary`, column bodies paged from the persisted `profile_json` artifact. `pages/Report.tsx`. No fabricated statuses; ADR-015 method badges rendered. 772/772 pytest, tsc+vite clean 2026-10-25. **→ NEXT: M6-T5.**
- [x] **M6-T5** Report View: Visualizations + Quality Assessment. *Spec: expected-output §3.3–3.4.* ✅ Client-side Plotly (histogram/categorical-freq/missing-rate/IQR-box, §3.3) built ONLY from the M6-T4 `report.get_columns` bodies — NO new §5.2 command; chart shapes mirror `reporting/html_report.py` `_charts_for_column`/`_box_spec`/`_missingness_chart` for parity. Quality Assessment (§3.4) = paged `quality.get_results` table + status/dimension filters, FAIL≠ERROR kept distinct (§4.1), ADR-015 method badges. Files: `frontend/src/charts/plotly.ts` (new — spec builders + lazy vendored-Plotly loader), `charts/PlotlyChart.tsx` (new — draw/purge), `vite-env.d.ts` (new — `window.Plotly` ambient), `pages/Report.tsx` (+Visualizations +Quality sections, MethodTag, ChartFigure), `styles.css` +31. Plotly wiring: `vite.config.ts` `publicDir="../data_profiling_engine/reporting/assets"` reuses the ONE vendored 4.8 MB bundle (no npm dep, no git duplication) → copied to `dist/plotly.min.js`; UI loads it with a DOCUMENT-relative `<script src="plotly.min.js">` so it resolves offline under `file://` (no CDN/network, ADR-005/009/010). GOTCHA: a `?url` import of the outside-root asset FAILS rollup resolution ("Could not resolve … ?url") — use publicDir + document-relative src, not `?url`. No Python/contract change (QualityCheckResult/QualityGetResults* already in contracts.ts, parity test green). 772/772 pytest, tsc clean, vite build clean (dist/plotly.min.js present) 2026-10-25. **→ NEXT: M6-T6.**
- [x] **M6-T6** Report View: Findings & Recommendations + Export actions. *Spec: expected-output §3.4–3.5.* ✅ **Required a contract change**: added the §5.2 read command `recommendations.get` (**ADR-020**) — a Finding (§4.2) carries only `recommendation_ids`, and no existing command returned the §4.3 recommendation *bodies* (action/rationale/do_not/reason_type); `report.get_summary` is counts-only and the bodies lived only in the `profile_json` artifact behind `artifact.open` — the exact ADR-019 column-body gap, raised per AGENTS §8 and approved before coding. Findings via `findings.get` (paged, severity/category filters, fingerprint + §7 fact/interpretation/hypothesis separation rendered apart); recommendation bodies via `recommendations.get` (paged, optional `finding_id`, resolved against the SAME run_id). Export (§3.5) = `artifact.open` for the three run-recorded kinds (`html_report`/`profile_json`/`issue_csv`, from `summary.result_refs`) — ack + reveal, no bytes cross. Files: `bridge/envelope.py` (+`RecommendationsGetPayload`), `bridge/facade.py` (+`_recommendations_get_handler`, COMMANDS, docstring seven→eight reads), `storage/metadata_store.py` (+`count_recommendations`, paged/filtered `list_recommendations` kwargs — defaulted, `load_recommendations` caller unaffected), `docs/CONTRACTS.md` §5.2 row + note, `docs/ARCHITECTURE_AND_ADRS.md` ADR-020 + matrix row, tests `test_bridge_read_commands.py` (+3: exact-set +1, contract-shape/paging/finding_id-filter). Frontend: `types/contracts.ts` (+RecommendationsGetPayload/Result), `pages/Report.tsx` (+Findings/Recommendations cards +Export section, FindingCard/RecommendationBlock, run_id state pattern from T5), `styles.css` +71. No schema/model change (Finding/Recommendation already in pydantic↔TS parity). 775/775 pytest, tsc clean, vite build clean 2026-10-25. **→ NEXT: M7-T1 (M6 UI screens COMPLETE).**

## M7 — Hardening & Release Gate

- [x] **M7-T1** Benchmark harness + synthetic generator; baseline numbers recorded into TESTING §4.3. *Spec: TESTING §4.* ✅ `data_profiling_engine/benchmark/` — `generator.py` (`DatasetSpec`, DuckDB-side `COPY … FROM range(n)` synthetic CSV/Parquet, deterministic hash-salted values — never a Python row loop, ADR-007; core kinds pk_id/amount/stage/category/event_date + alternating extras, card/missingness/distribution/seed knobs), `harness.py` (`measure_case` runs each profile in a **fresh child process** so `resource.getrusage` RSS is per-run isolated; pure `evaluate_rss_boundedness` / `evaluate_sampled_cheaper`; `run_suite` emits a self-describing results JSON + machine context), `__main__.py` (`generate|measure|baseline`). `baseline_macos_dev.json` (tracked) = dev-mode baseline; TESTING §4.3.1 records it: RSS 94→169 MB across 200k→1M (×5 rows, ×1.8 RSS = **bounded**, no Python materialization); sampled 144.7s < deep-exact 157.5s (**degrade strictly cheaper**, marginal — full-relation quality rules + query-count dominate). Fast regression test `tests/test_benchmark_harness.py` (+13, never runs the heavy ladder). **GOTCHA:** macOS `os.sysconf` has no `SC_PHYS_PAGES` → RAM read via `sysctl hw.memsize`; `resource` absent on Windows → RSS `None` there (Windows/packaged baseline owed at §5.1). 788/788 pytest green 2026-10-25. **→ NEXT: M7-T2.**
- [x] **M7-T2** Scale guarantees verified (RSS flatness, cancel latency, recovery). *Spec: TESTING §4.3.* ✅ Landed as **M7-T2b** (cancellation latency + crash recovery, TESTING §4.3.2) and **M7-T2a** (the scheduled 10M→100M RSS-curve ladder, TESTING §4.3.3 + `benchmark/ladder_10m_100m_macos_dev.json`): verdict **`bounded`** (`rss_ratio 5.832 < row_ratio 10.0`), both tiers `SUCCEEDED` / 0 errors — 10M 1310.2 s / 677.0 MB, 100M 9843.7 s / 3948.2 MB. **Honest caveat recorded in §4.3.3:** bounded ≠ flat; the joined 200k→100M series grows ≈ `rows^0.766` (corrected from the first draft's `0.65` by fitting the pair properly), extrapolating to ≈22.5 GiB at 1B rows, so the §4.2 "must stay flat" claim is NOT yet proven at that scale → follow-up task below. Also added the read-only `benchmark watch` % -done indicator (11 tests). 812/812 pytest green 2026-10-25. **→ NEXT: M7-T2c.**
- [x] **M7-T2c** Explicit memory ceiling + spill verification, then re-measure the top of the ladder. *Spec: BLUEPRINT Phase 3 "memory ceilings"; TESTING §4.2 (peak RSS) / §4.3.4.* ✅ **ADR-021** raised first (AGENTS §8 — the ceiling value is a product decision, not a silent pick) + new `data_profiling_engine/duckdb_session.py` as the ONE place a connection opens: `memory_limit = min(4 GiB, 25 % RAM)` floored at `2 × threads × 128 MiB` (clamped [512 MiB, 4 GiB] → 1 GiB on 4 cores) and held under 90 % of RAM, owner-only (0700) `temp_directory` under `storage.paths.default_temp_root()` replacing the inherited **relative** `.tmp` (SECURITY §2 hazard under PyInstaller), 8 GiB spill cap, `SET` then **read back** with a 1 % tolerance, overrides via `DPE_DUCKDB_*` env/kwargs and deliberately NOT §9 dataset config (no contract/`config_hash` change). All 5 bare connect sites routed through it + a source-level guard test that fails CI on a new bare `duckdb.connect()`. Measured at 10M on the retained fixture (TESTING §4.3.4, `benchmark/bounded_ceilings_macos_dev.json`): 4 GiB → 675.1 MiB / 1218.8 s / 0 err; 1 GiB floor → 631.6 MiB / 1251.2 s / 0 err; 256 MB → 375.7 MiB + 146.3 MiB spill but **28 ERRORs** (non-spillable 128 MiB quantile buffer — the reason the floor is derived, not chosen). Reading: the ceiling BINDS but never shrinks a run below its working set, so `evaluate_rss_plateau` (b ≤ 0.25 + inside the ceiling) ships as a DIAGNOSTIC while `evaluate_rss_boundedness` stays the binding gate. **100M under the shipped default (the last owed number):** 8847.8 s / **3724.0 MiB** / **87.7 MiB peak spill** (sampler; 0 B left on disk) / 0 err `SUCCEEDED` = −5.7 % RSS vs the unbounded 3948.2 MiB, yet still `rows^0.742` (0.766 unbounded) because 90.9 % of the 4 GiB ceiling means the working set *fits* — it touches the ceiling, it is not shaped by it. 866/866 pytest green 2026-10-25 (no frontend change). **Still owed (M7-T3 era, not M7-T2c):** a forced ceiling BELOW the 100M working set (only direct flattening proof) + the series on the PACKAGED build (§5.1). **→ NEXT: M7-T3.**
- [~] **M7-T3** Both-OS installer matrix: install→job→export→upgrade→uninstall. *Spec: SECURITY §6; TESTING §5.1.* ✅ **macOS packaging track LANDED (dev box, UNSIGNED).** Repo-side tooling now in `packaging/` (a NEW top-level dir — ARCHITECTURE §3 covers only the engine package, so build scripts sit at repo root beside the `.spec`, matching `data_profiling_engine.spec`): `macos/build_app_dmg.sh` (PyInstaller → `hdiutil` `.dmg`, no 3rd-party dmg tool), `macos/sign_notarize.sh` (Developer-ID codesign+notarize, **credentials via env, never in repo** — AGENTS §7), `macos/entitlements.plist`, `windows/profiler.nsi` (NSIS, per-user `$LOCALAPPDATA`, uninstall preserves `%APPDATA%` data per §6.5, WebView2 detect §6.4), and the operator runbook `docs/M7_T3_INSTALLER_MATRIX.md` (§5.1 checklist + macOS/Windows tracks). **Spec rewrite:** the frozen app is now a **windowed (console=False) onedir `.app` on macOS** (`COLLECT`+`BUNDLE`) and **still a onefile console `.exe` on Windows** (unchanged from the M0 spike — a windowed Win exe can't capture `--smoke > smoke_win.txt`). Rebuilt `dist/` from current code (was the stale Oct-24 binary) and the packaged `--smoke` is **green on all four §5.2 markers**: DUCKDB 2.0.0 rows=[(42,)] / WORKER `status=SUCCEEDED` 4/4 (frozen **spawn** worker) / BRIDGE echo `status=ok` / `SMOKE_OK` `file://` from `…/Contents/Frameworks/frontend/dist/index.html`. Built + `hdiutil verify` VALID the unsigned `dist/DataProfilerEngine-0.1.0-macos.dmg` (45 MB). GOTCHA (spec): PyInstaller 6.22 `BUNDLE()` kwarg names are **`bundle_identifier=` / `info_plist=` / `version=`** — the old `id=`/`info=` are silently ignored and left CFBundleIdentifier=`DataProfilerEngine`/version 0.0.0 (caught by diffing Info.plist). GOTCHA (smoke): the `--smoke` probes use the M0 spike commands (`echo`/`worker_echo_job`) which the packaged facade registers ONLY behind `DPE_ENABLE_SPIKE_COMMANDS=1` (spike OFF in production is correct — a bare `--smoke` rejects them as `BRIDGE_COMMAND_UNKNOWN`); run the packaged smoke with that env set. GOTCHA (sandbox): `hdiutil create/verify` need disk arbitration → fail `Operation not permitted` inside the restrictive sandbox, run outside. ⛔ **BLOCKED — cannot close from this box:** (1) the whole **Windows** track (BLUEPRINT Q4 "physical/virtual Windows required, not optional") → handed to the §4 operator runbook; (2) macOS **codesign + notarization** — `security find-identity` shows **0** Developer ID identities here, and AGENTS §7 forbids handling creds → handed to §3 release env. **Follow-up (2nd M7-T3 commit):** persisted the Windows operator runbook `docs/WINDOWS_M7_T3_CHECKLIST.md` (mirrors `WINDOWS_M0_T5_CHECKLIST.md`, extended to the full §5.1 install→job→export→upgrade→uninstall matrix) and added the missing **Windows peak-RSS** path so §5.1 "benchmarks documented" produces a real number instead of a caveat: `benchmark/harness.py` now wraps the measured child in a `_RssPeakSampler` (**psutil** self-poll @ 50 ms, self+children) when POSIX `resource` is absent, labelling every case with `peak_rss_method ∈ {resource.ru_maxrss, psutil_sampled_peak, unavailable}` — a Windows **sampled** peak, never presented as the POSIX high-water (ADR-015). New optional dep `psutil>=6.0` in `requirements.txt` (benchmark tooling only; never bundled in the frozen app). +4 pure tests in `test_benchmark_harness.py` (label honesty + inert-without-psutil; the positive-peak test skips where psutil absent). This commit DID touch package code (`benchmark/harness.py`) + `requirements.txt` + docs — the earlier macOS-track commit was spec/packaging/docs only. 58 passed / 1 skipped across the benchmark test group 2026-10-26 (no engine/contract change; full suite unaffected). **→ NEXT: M7-T4** (security acceptance automation) unless a Windows box + Developer ID become available to finish M7-T3's two blocked legs.
- [x] **M7-T4** Security acceptance automation. *Spec: SECURITY §8; TESTING §5.4.* ✅ New `tests/test_security_acceptance.py` — one class per §8 bullet (34 tests): §8.1 static no-server/no-bind scans over engine+frontend sources + a LIVE psutil process-tree listener scan during a real `profile.start` + stdlib-only `PORTS_SMOKE_RESULT`/`PORTS_SMOKE_FAILED` self-check now wired into `main.py --smoke` (lsof `-iTCP -sTCP:LISTEN` / `netstat -ano -p TCP` filtered to app pids — psutil stays OUT of the frozen bundle); marker lines added to `docs/M7_T3_INSTALLER_MATRIX.md` §1 + `docs/WINDOWS_M7_T3_CHECKLIST.md` §4 and TESTING §5.4 points at the module. §8.2 packaged-facade rejections (unknown cmd / bad schema / out-of-scope path both source+artifact-ref / oversized request AND response) with a `SECRET` no-echo assertion. §8.3 real-run default HTML+CSV+preview carry no raw PII. §8.4 credential-name walk of every schema/model, no-keyring-imports scan, credential-shaped config refused pre-dispatch, planted secret survives nowhere (documents, artifacts, error_log, raw SQLite bytes). §8.5 copytree-isolated upgrade-preserves-rows(+backup)/cleaners-never-touch-referenced-files/app-bundle-refusal/NSIS-uninstall-and-macOS-scripts-delete-no-user-data/forward-only-migrations. **The suite immediately caught TWO real SECURITY §4 bugs in `reporting/html_report.py`** (fixed here): the per-column detail loop never recomputed `pii` — it inherited the LAST header-table column's flag, so an email column's Categories rendered raw whenever a non-PII column came last; and `rare_categories` were emitted with NO masking at all. GOTCHA (discovery): pattern refinement samples DISTINCT values (`SAMPLE_DISTINCT_LIMIT=32` reservoir), so a column needs **≥3 distinct** pattern-matching values before email/phone tagging fires — a 2-distinct phone column stays `categorical_nominal` (test fixture widened to 3 distinct phones accordingly; ENGINE §4.1 "≥3 to avoid single-value false hits", by design). GOTCHA (venv): `.venv` is uv-managed — no pip inside; use `uv pip install --python .venv/bin/python <pkg>` (psutil 7.2.2 now installed so the live §8.1 + peak-RSS tests run instead of skipping). 904/904 pytest green (0 skipped) 2026-10-26; no frontend/contract change. **→ NEXT: M7-T5 (GATE MVP) — but it CANNOT pass until M7-T3's Windows + codesign/notarize legs run green on packaged builds; rebuild `dist/` first so the new PORTS marker is in the shipped binary.**
- [x] **M7-T6** ADR-023 — pywebview loopback HTTP server vs SECURITY §8.1 (escalated finding → Accepted Option A). *Spec: ADR-005/023; SECURITY §8; TESTING §5.1.* ✅ The macOS installer rebuild's packaged `--smoke` printed `PORTS_SMOKE_FAILED: 1 listening socket(s) … 127.0.0.1:<ephem> (LISTEN)`. Root cause verified against the installed pywebview 6.2.1 / bottle 0.13.4 (not assumed): `webview.start()` auto-fires its bottle server on `http_server or has_local_urls` because the SPA loads from `file://` (ADR-010/013), even with the default `http_server=False`; the server binds `127.0.0.1` only, serves ONLY the bundle's static files, and carries NO data/bridge traffic (macOS js_api rides the native `jsBridge` WKScriptMessageHandler; the bottle `/js_api` POST route is inert — `js_callback` is init'd `{}` and never populated). Escalated per AGENTS §8, user chose **Option A** (bound & document, no load-path change) over Option B (eliminate the server via a custom-scheme load — re-risks the packaged content-load path at the release gate; kept as post-1.0 cleanup). `main._run_ports_smoke` refactored into pure, unit-testable helpers (`_LOOPBACK_HOSTS`, `_split_host_port`, `_parse_listener_lines`, `_pywebview_loopback_endpoint`, `_select_port_offenders`) that allow-list EXACTLY pywebview's live loopback `(host,port)` — a second loopback socket, a non-loopback/LAN bind, or any our-pid socket when no server runs still FAILs. New `tests/test_ports_smoke.py` (+10: parsing, offender selection, loopback-gated endpoint derivation, and a guard asserting the adapter default bind is `127.0.0.1` + `BottleServer.js_callback=={}` — the inert-HTTP precondition). `requirements.txt` pins `pywebview>=6.2,<7` (an upgrade that flips the binding invariants fails the suite instead of silently exposing a listener). Docs: ADR-023 → Accepted (Option A), ADR-005 clarified, decision-matrix row updated, SECURITY §8 + §1 + TESTING §5.1 amended to "no **app-defined** port" scoping, both installer runbooks' PORTS line now expects `PORTS_SMOKE_RESULT: only_pywebview_loopback`. **CORRECTED misstatement:** the `--smoke` `echo`/`worker_echo_job`/`job.get_status` probes are NOT stale — intentional M0 spike commands gated behind `DPE_ENABLE_SPIKE_COMMANDS=1`; left UNTOUCHED (only the ports check needed the fix). 920/920 pytest (910 + 10) + packaged rebuild `--smoke` with spike env → `PORTS_SMOKE_RESULT: only_pywebview_loopback …` + `SMOKE_OK` 2026-10-26. **→ NEXT: M7-T5 (GATE MVP).**
- [~] **M7-T5** **→ GATE MVP: every box of TESTING §5.1 green on packaged builds.** *Spec: TESTING §5.1; SECURITY §6.6; runbook `docs/M7_T3_INSTALLER_MATRIX.md` §1–§2.* **GATE NOT STAMPED.** Run 1 (2026-10-26, macOS dev box, HEAD `519914e`, working tree clean) = the smoke-provable subset only. Provenance: `dist/` rebuilt with `.venv/bin/python -m PyInstaller --clean --noconfirm data_profiling_engine.spec` (EXIT=0, 247 s, `.benchmarks/m7t5/pyinstaller_build.log`), `.app` @ 10:05, DMG re-cut @ 10:16 (45 335 447 B, `hdiutil verify` VALID, UNSIGNED); packaged `--smoke` under `DPE_ENABLE_SPIKE_COMMANDS=1` → EXIT=0 (`smoke_packaged.log`); suite `925 passed / 367.77 s` (`pytest_head.log`). §5.1 board (**15 GREEN · 2 PARTIAL · 0 OPEN**, box 11 stamped 2026-10-26 run 3) — the authoritative per-box record, refreshed with the run-2 walk on the installed `.app`:

  | # | §5.1 box | Status | Evidence / gap |
  |---|---|---|---|
  | 1 | CSV **and** Parquet load, no other connector (ADR-011) | **GREEN** | installed `.app`: `run 0aa42cd5…` (CSV) and `run 1256d929…` (Parquet) both loaded through Dataset Selection; only the two ADR-011 connectors exist |
  | 2 | summary + schema profile, physical vs logical types | **GREEN** | inspect panel (`gui_inspect*.png`) + Report → Schema & Column Profiling show VARCHAR/string, DATE/temporal_date, BIGINT/numeric_currency |
  | 3 | statistics + distributions + missingness + uniqueness | **GREEN** | `run 0aa42cd5…`: missingness+uniqueness on all 5 columns, `distribution` on the numeric one, `categorical`/`temporal` where applicable |
  | 4 | rules run; PASS/FAIL/WARN/ERROR distinguished | **GREEN** | `deal_id_unique FAIL` / `value_not_null FAIL` / `stage_domain PASS`; UI scorecard `PASS 1 · FAIL 0 · WARN 0 · ERROR 0` on the small fixture |
  | 5 | findings carry evidence + metric/rule refs + `fingerprint` (ADR-016) | **GREEN** | 5 findings / 5 distinct `sha256` fingerprints, `metric:`+`rule:` `evidence_refs`, UI renders `fingerprint: sha256:6ede1d01…` + `column stage · 3 affected records` |
  | 6 | recommendations assert no unproven causes | **GREEN** | 5 recommendations with `do_not` guardrails; hypotheses stay `status=unverified` + `suggested_probe`, rendered apart from facts in the UI |
  | 7 | method labels `exact/approximate/sampled` + seed (ADR-015) | **GREEN** | packaged `sampled`+seed run `095f3987…` (2M rows, reservoir / window 100000 / seed 424242): 67 `exact` + 9 `sampled` + 1 `not_computed`(reason); every sampled leaf carries `{strategy: reservoir, sample_size: 100000, seed: 424242}`, `row_count` stays `exact` 2 000 000; UI shows `Sampling seed 424242` + `sampled` badges on all 8 Distinct cells against `exact` chart chips; head-to-head with the exact-mode run on the same fixture = **0 sampled values labelled exact**. The step-7 gap (bare `is_unique_candidate` derived from the sample) is FIXED and re-proven on the rebuilt binary `63257fbff18fba71`: packaged run `d6cf005a…` (20 000 rows, window **5000**, seed **424242**) = 48 `exact` + 6 `sampled` + 1 `not_computed`, and packaged run `0edba2e9…` (same fixture, sampling OFF, 20 000 > 10 000 ⇒ `approximate`) = 49 `exact` + 5 `approximate` + 1 `not_computed`; in BOTH, `is_unique_candidate` == independent DuckDB full-relation truth on every column (`box7_derived_probe.py`, `RESULT: PASS`) — incl. `id` **true** where the HLL said 20 234 ≠ 20 000 (the old bug's false negative) and `near_uniq` **false** where the 5 000-row sample alone would have claimed true. See "Run 2, step 7b" |
  | 8 | HTML + JSON from one result, CSV export, HTML self-contained | **GREEN** | `run 872fc949…`: `report.html` 4 839 726 B / `profile.json` 23 361 B / `issues.csv` 91 B (header only — 0 FAIL/WARN rows is correct, the CSV carries issue rows only) all from ONE result document; self-containment re-verified on the revealed file (0 `@import`, 0 `url(http…)`, only `src=""` + a `data:` URI); all three **`artifact.open` export clicks acked in the packaged UI** 2026-10-26 with the exact byte counts (`gui_box8_export_html|json|csv.png`) |
  | 9 | run_id, config version/hash, status, duration, error log, engine version | **GREEN** | run row carries run_id / config_hash `sha256:9e9c673c…` / config_version 3 / engine 0.1.0 / duckdb 2.0.0 / python 3.12.4 / duration_ms / status / started_at / finished_at |
  | 10 | worker (not UI thread), UI responsive, job observable + cancellable | **GREEN** | 3a cancel → CANCELLED **4.3 s** after the click, no run row, no artifact dir; 3b ladder `2/14 … 14/14 · 100 %` SUCCEEDED in 206.2 s |
  | 11 | worker crash does not corrupt store; recovery on restart | **GREEN** | packaged, 2026-10-26: worker SIGKILL mid-run → live mirror FAILED/WORKER_CRASHED ≤0.5 s + UI banner (`box11_37_progress_failed.png`); host SIGKILL with job RUNNING → next `profile.start` reconciled the orphan to FAILED/WORKER_CRASHED; both legs 0 orphan runs rows, `integrity_check`+`foreign_key_check` ok, sources unmodified, post-crash run SUCCEEDED 14/14 (`job_c6f25d80cfa1`). Dev-mode automation (`test_worker_job`, §4.3.2) agrees |
  | 12 | sources never modified | **GREEN** | fixture sha256s after every packaged run == `.benchmarks/m7t5/fixtures_pre_sha256.txt` |
  | 13 | bridge-only, **no app-defined port** (ADR-005/023) + HashRouter over local files (ADR-013) | **GREEN** | `PORTS_SMOKE_RESULT: only_pywebview_loopback host=127.0.0.1 port=45054 pids=[28870, 28880]` + `SMOKE_OK … _MEIPASS/frontend/dist/index.html` |
  | 14 | no local dev server required for the packaged run | **GREEN** | packaged load is `file://` from the bundle; no vite/dev server in the run |
  | 15 | unit + integration tests green; `sales_deals` yields known findings | **GREEN (dev env)** | `925 passed in 367.77 s` @ `519914e`; the M4-T1 golden harness pins the `sales_deals` findings — the packaged-env execution folds into box 16 |
  | 16 | installer matrix both OS: install→launch→job→export→upgrade→uninstall | **PARTIAL** | macOS legs all verified on the installed `.app`: install (run-2 DMG), launch, one full job, export artifacts, upgrade (rows + `schema_migrations=2` preserved) and **uninstall (data preserved, 2026-10-26 — 21 files / 26 129 241 B byte-identical)**. Windows leg **REPORTED GREEN 2026-10-26** (`fe70bbe` build, Win 11 25H2 x64, 947/947 parity, golden byte-identical): install→launch→job→export→upgrade(0.1.0→0.1.1)→uninstall, user data `%APPDATA%\DataProfilingEngine` byte-identical across all three snapshots (2 files / 269,243 B, `metadata.sqlite3 621d1a72e94a`+artifact `7aaec173d45b`, rows 1/5/3/5/1 preserved, `user_version 2→2`). **GUI screenshot addendum DONE on the installed GUI 2026-10-26** (13 live WebView2-window captures under `_gate_evidence_win\gui\`): box 2 post-run schema (logical populated: string/temporal_date/categorical_nominal/numeric_currency) + box 3 column detail, box 5 `fingerprint: sha256:91c4e1e88b8a…`, box 6 red "Do not assume the duplicates are errors…" guardrail + separate HYPOTHESES, box 7 exact `(exact)` tag AND sampled run (8 `sampled` badges + `Sampling: seed 20261024`), box 8 three export acks + `gui_box08_pii_masked.png` (email/phone 🔒 masked, "Masked columns: email, phone"), box 10 mid-run `RUNNING 10/14 · 71%` with title-bar-drag+nav responsive, box 13 live `netstat` = single `127.0.0.1` pywebview loopback. Bridge banner ABSENT (useBridgeReady confirmed on Windows). GUI-line caveat from the automated leg is now CLOSED. Non-defect findings logged: F-1 physical-vs-logical lives in the per-column detail header (see GOTCHA below), F-2 exact is unbadged by design, F-3 window-automation quirk, F-4 ids are `job_<hex>`. **Now the ONLY open sub-item on this row is the macOS codesign/notarize distributable (0 Developer ID here) — the installer matrix itself is proven on both OS.** |
  | 17 | benchmarks documented with recorded baselines | PARTIAL | TESTING §4.3.1–§4.3.4 dev baselines + ADR-021 ceilings recorded, plus a **packaged** macOS peak (UI 256.0 MiB / worker 263.1 MiB / app 562.4 MiB, `psutil_sampled_peak`). **Windows packaged baseline REPORTED GREEN 2026-10-26** (matched the macOS workload `[200k,500k,1M]`+degrade pair, honest `psutil_sampled_peak` label; `rss_boundedness: bounded` ratio 2.127, `sampled_cheaper_than_exact: cheaper` 1.14; `guarantees` cancel 0.765 s bounded + crash-recovery 0.046 s consistent, orphan_runs 0). **macOS packaged 10M→100M ladder RUNNING 2026-10-26 22:23** (`nohup bash .benchmarks/ladder/run_packaged.sh`, parent pid 78112, out `.benchmarks/ladder/packaged_10m_100m_macos.json`, ETA ~3.1 h ≈ 1310 s + 9844 s): the packaged `.app` has no benchmark entry (spec Analysis entry = `main.py` only), so per TESTING §4.3.3 the packaged baseline IS the module `baseline`/`guarantees` run on the OS — same method the Windows leg used. New `--reuse-fixtures` flag profiles the RETAINED `csv_{10m,100m}_std.csv` as-is (verified: child cmd is `measure --input …std.csv`, fixture mtimes stay 2026-10-25, no regeneration on the 97%-full volume). **NEXT on this box: when the JSON appears, verify verdict `bounded` + copy to `benchmark/ladder_10m_100m_macos_packaged.json`, then box 17 = GREEN on both OS; still owed after that is only the macOS codesign/notarize leg.** CAUTION: `benchmark watch` reads the RETAINED `csv_100000000_std.measure.json` from the 2026-10-25 dev run and will falsely report the 100M tier as `done`/overall `RUN COMPLETE` until this ladder's own 100M pass unlinks + rewrites it (`measure_case` unlinks its emit at tier start); trust the appearance of `packaged_10m_100m_macos.json`, not watch, for completion. |

  **Do NOT stamp GATE MVP on this evidence yet.** The Windows legs of boxes 16 + 17 are now **verified green by this macOS session** (report claims cross-checked against `facade.py`/`Report.tsx`/NSIS + the byte-identical golden; the GUI screenshot addendum closed the box-16 visual caveat). Boxes 16/17 still stay PARTIAL pending only: (a) the **macOS codesign/notarize** distributable (needs a Developer ID — still blocked on this box), and (b) box 17's **10M→100M packaged ladder** to confirm the ADR-021 plateau (runnable here, long). SPEC DOC UNCHANGED: `docs/TESTING_BENCHMARKS.md` §5.1 checkboxes stay unticked (the gate is still open); this board is the evidence log.

  **Run 2 (installed app, in progress).** Installed from the run-1 DMG to `/Applications` (copy, `spctl --assess` → **rejected**, ad-hoc signature only) and re-verified there: 6/6 §5.2 markers green incl. the new `SCHEMAS_SMOKE_RESULT` (`.benchmarks/m7t5/smoke_installed_run2b.log`) ⇒ boxes **13 + 14 GREEN on the installed artifact** (not just the bundle). The upgrade leg of box 16 is exercised (bundle re-copied over the install; `~/Library/Application Support/DataProfilingEngine` untouched, `schema_migrations=2`); the macOS **uninstall leg is done as step 4 below**, Windows + codesign/notarize still BLOCKED. Boxes 1–12 + 17 are walked on the installed app — started by the user, and now by the agent itself, since assistive access was granted mid-session (`System Events` reads the AX tree, types and `click at {x,y}`; every step is screenshot-verified, see the computer-use note at the top), with fixtures `sales_deals.csv` / `sales_deals.parquet` (40 rows) + `run2_big.csv` (2 M rows) staged under `.benchmarks/m7t5/fixtures/` and sha256-pinned for the box-12 "sources unmodified" check. The first walk attempt hit the schemas defect above — the packaged binary could not start ANY job, which is precisely what the packaged gate exists to catch.

  **Run 2 evidence so far (agent-verified from disk).** `run 0aa42cd5…` (CSV,
  3.5 s) and `run 1256d929…` (Parquet, 1.2 s) = boxes 1–9; `run 36ab428f…`
  (2 M rows × 8 cols, 248.5 s) = the large-source leg. Box 17 RSS on the
  packaged build, sampled 1 s while that job ran (`psutil_sampled_peak`,
  `.benchmarks/m7t5/packaged_rss_samples.log`): UI process peak **256.0 MiB**,
  worker peak **263.1 MiB**, whole-app peak **562.4 MiB** total — no OOM, no
  swap pressure, native DuckDB spilling allowed. Cancel (`job_6d03573bd0ba`)
  confirmed CANCELLED with **no runs row** written, i.e. §6.5 "nothing durable"
  holds; its 4 min 18 s latency was the defect fixed above.

  **Run 2, steps 3a/3b (agent-driven on the installed app, 2026-10-26).** With
  `run2_big.csv` (2 M rows, 122 MB, `standard`, sampling off, one rule
  `stage not_null`) the fix is now visible end to end. **3a cancel:**
  `job_59f487377900` started 05:53:34Z, Cancel clicked at 05:54:33Z while it sat
  on `4/14 profiling:statistics_missingness`; store showed `CANCEL_REQUESTED`
  immediately and `CANCELLED` **~4.3 s** after the click, ending at
  `5/14 profiling:uniqueness_categorical` — no run row, no artifact dir
  (§6.2/§6.5 hold). **3b observable progress:** `job_76cefccb7898` walked the
  ladder `2/14 discover → 3/14 plan → 4/14 statistics_missingness → 5/14
  uniqueness_categorical → 6/14 distributions_temporal → 10/14 dataset_metrics →
  14/14 persist:metadata` and ended **SUCCEEDED, 14/14 · 100 %** in 206.2 s
  (`run b33f4455-e2f6-479d-8596-19854ef4021d`; artifacts `profile.json` 60 KB,
  `report.html` 4.6 MB, `issues.csv` 202 B; run row carries run_id / status /
  engine 0.1.0 / duckdb 2.0.0 / python 3.12.4 / config_hash `sha256:1f6c2a6a…`
  / duration_ms / started_at / finished_at) ⇒ box 10 GREEN, and box 11 partly
  evidenced by the UI itself (an empty-column rule blocks Start with
  `Validation Issues: rule 1: select the column it checks`). Fixture sha256s
  re-checked after all of it: identical to `.benchmarks/m7t5/fixtures_pre_sha256.txt`
  (box 12). Screenshots: `gui_progress_page.png`, `gui_cancel_requested.png`,
  `gui_cancelled.png`, `gui_3b_midrun.png`, `gui_3b_final.png`, `gui_report_page.png`;
  log `.benchmarks/m7t5/run_3b.log`.

  **Run 2, step 4 — box 16 UNINSTALL LEG (agent-driven, 2026-10-26).** App quit
  (no `DataProfilerEngine` process left), then removed the way a macOS user
  removes it: the bundle moved out of `/Applications` (Finder "Move to
  Trash"), after which `open -a` refuses with `kLSAppInTrashErr` — macOS has
  no uninstaller script, so the bundle *is* the installation. A stale install
  DMG (`/Volumes/DataProfilerEngine`, mounted since 10:15) was ejected too.
  User data was snapshotted before/after with `box16_inventory.py`, which
  **copies the SQLite trio aside and reads the copy** so the data root is never
  written to by the measurement itself: `box16_pre.json` vs `box16_post.json`
  compare **IDENTICAL** on `data_root` + `cache_root` + `db` — 21 files /
  26 129 241 B, `metadata.sqlite3` sha256 `dbf09cb8…`, every artifact sha256,
  `schema_migrations=[1, 2]`, and the same 5 runs / 7 jobs / 30 findings /
  9 checks / 15 artifacts. `box16_storeprobe.py` then replayed the real §5.2
  read handlers against a COPY of the post-uninstall root:
  `report.get_summary(dataset_id=sales_deals)` resolved the **pre-uninstall**
  run `872fc949…` with all three `result_refs`, plus `report.get_columns` 5 /
  `quality.get_results` 1 / `findings.get` 4 / `recommendations.get` 4 — the
  surviving store + artifacts are still fully consumable. The byte-identical
  bundle (`Info.plist` + executable sha256 match `dist/`) was reinstalled to
  `/Applications`, launched, and inspected `sales_deals.csv` again; the third
  snapshot `box16_post-reinstall.json` differs from pre-uninstall **only** in
  `metadata.sqlite3-shm` (SQLite shared-memory index, recreated when the app
  opens the DB) — the full diff is in `box16_post_uninstall.log`. Evidence:
  `box16_pre.json` / `box16_post.json` / `box16_post-reinstall.json` /
  `box16_post_uninstall.log` / `box16_storeprobe.log` /
  `gui_box16_reinstalled_launch.png` / `gui_box16_reinstalled_inspect.png`.
  GOTCHA (pre-existing UI gap, NOT caused by the uninstall): the Dataset
  Selection screen never renders the "Previously profiled" badge or the
  logical/semantic types because `DatasetSelection.tsx` sends only
  `source_path`, while `facade._resolve_run_id` needs `dataset_id`/`run_id` —
  so `last_run` is always null from the UI (the same `—` columns appear in the
  pre-uninstall `gui_inspect.png`). The surviving data was therefore proven
  through the bridge handlers rather than the badge; the one-line fix
  (`dataset.inspect` should fall back to its own `path.stem` dataset_id when
  resolving the run) belongs to a later UI task.
  Still owed: Windows track, codesign/notarize, a packaged `sampled`+seed run
  (box 7), a packaged worker crash (box 11), packaged benchmark baselines
  (box 17).

  **Run 2, step 5 — M7-T5c packaged bridge readiness (2026-10-26).** Driving the
  installed app surfaced a real gate-class defect, not a driving problem: the
  packaged UI rendered "Desktop bridge not detected" and disabled every read.
  Root cause (_not_ a missing PyInstaller asset — `Resources/webview/js/api.js`
  is in the bundle): `bridgeAvailable()` (`frontend/src/bridge/api.ts`) was
  evaluated **once at render** in all four screens, but pywebview injects
  `window.pywebview` after the page loads and only then dispatches
  `pywebviewready` (`webview/js/finish.js`). The packaged bundle mounts before
  the injection lands, so the banner latched on permanently; dev mode hid the
  race because Vite loads slower. Fix: new `frontend/src/bridge/useBridgeReady.ts`
  (seeds from the current state, listens for `pywebviewready`, bounded 100 ms
  poll with a 10 s give-up so a plain browser still degrades) wired into
  `DatasetSelection` / `RunConfig` / `RunProgress` / `Report`; `bridgeAvailable`
  stays as the predicate the hook and the bridge call paths share. Verified on a
  `--clean` rebuild + `ditto` reinstall: no banner on Dataset or Run, and a real
  `dataset.inspect` round-trip renders Schema & Metadata + the 5-column table
  (screenshots `/tmp/e0.png`…`/tmp/e4.png` on this box). `npm run typecheck` +
  `vite build` clean; no Python/contract change, so the pytest suite is
  unaffected (last full run 925 passed). No DOM test runner exists, so the fix
  is pinned by a static-source guard instead:
  `tests/test_frontend_bridge_readiness.py` (+3) fails if any component reads
  `bridgeAvailable()` at render again, if `useBridgeReady()` falls out of the
  hook, or if the hook loses the `pywebviewready` listener / bounded poll.
  Full suite 944 passed 2026-10-26 (0 skipped).

  **Run 2, step 6 — box 8 UI-acked export (2026-10-26).** On the installed app,
  Report screen (resolved `run 872fc949…` via `dataset_id=sales_deals`), all
  three `artifact.open` buttons were clicked and each returned its ack with the
  real artifact size: **HTML report 4 839 726 B**, **JSON result 23 361 B**,
  **CSV issue export 91 B**. The sizes match the files on disk exactly
  (`report.html` / `profile.json` / `issues.csv` in the run's artifact dir), so
  the ack is a real read of the recorded artifact, not a UI string. The CSV is
  header-only because that run has **0 FAIL/WARN** rows (1 PASS) — correct by
  design (the CSV carries issue rows only), so a 91 B file is the honest
  output, not a bug. Self-containment re-verified on the file the UI actually
  revealed: `@import` 0, `url(http…)` 0, the only `src` values are `src=""` and
  a `data:image/png;base64` pixel. Evidence:
  `.benchmarks/m7t5/gui_box8_export_html.png` / `…_json.png` / `…_csv.png`.
  No product code changed. GOTCHA (see GOTCHAS): macOS swallows the first click
  on a non-key window and the reveal puts a viewer above the app, so mid-walk
  clicks silently did nothing until `cgui.py clickapp` (activate + click in one
  process) was used — a driver artifact, not an app defect.

  **Run 2, step 7 — box 7 packaged `sampled`+seed run (2026-10-26).** Driven on
  the installed `.app` (cgui.py): `run2_big.csv` (2M rows, 8 cols) → Sampling
  ON (`reservoir`, size `100000`, seed `424242`) → `standard` → one rule
  (`pk_id not_null`) → `job_3838af846188` → **SUCCEEDED 14/14 in 186.9 s**,
  `run 095f3987-da80-4b5b-b2fa-075572389aa1`. Evidence
  (`box7_probe.py`/`.log`, `box7_exact_vs_sampled.log`, `box7_1x_*.png`):
  `run.sampling` = `{enabled, strategy=reservoir, sample_size=100000,
  seed=424242}`; 77 metric leaves = 67 `exact` + 9 `sampled` + 1
  `not_computed` (reason: no declared temporal range); **every** sampled leaf
  carries seed+strategy+window (the probe fails the run otherwise);
  `row_count` stays `exact` 2 000 000; head-to-head with the earlier exact-mode
  run `b33f4455…` on the same fixture → **0 values labelled exact that came
  from a sample**. UI: Overview `Total rows 2,000,000 (exact)` + `Sampling
  seed 424242`, all 8 `Distinct` cells badged `sampled`, chart chips `exact`
  (side-by-side contrast, `box7_16/17`). Fixture sha256 unchanged (box 12).
  **Gate-class gap found, NOT fixed — needs a decision:** the planner allows
  `sampled` for `requires_full_relation` metrics in standard/quick (only deep
  refuses, ENGINE §2.2), and `profiler._view_for` then evaluates
  `uniqueness.is_unique_candidate` on the SAMPLE while the §3.2 contract types
  it as a bare `bool` — no `provenance` slot, so a sample-derived claim is
  indistinguishable from a full-scale one (here `pk_id` → `true`, which DuckDB
  confirms IS true at full scale: 2 000 000/2 000 000). The same slot inherits
  approximation: the standard run derived `false` from the HLL estimate
  (1 903 536) on a column that IS unique — a false negative. Options raised
  with the user: (A) planner refuses `sampled` for `requires_full_relation` in
  EVERY mode (no contract change; also loses the honest sampled
  `temporal.gaps`), (B) give the derived surfaces a provenance carrier (§3.2 /
  CONTRACTS change), (C) always compute them on the full relation (what
  `_key_candidates` already does, costs one exact count(DISTINCT) per column).
  No product code changed in step 7 (the fix landed in step 7b, below).

  **Run 2, step 7b — derived bool moved to the full relation, box 7 GREEN
  (2026-10-26).** User decision (asked per AGENTS §8): option **C, full-relation
  derive** — no `CONTRACTS §3.2` shape change. Code: `engine/metrics/profiler.py`
  Pass 2 now `continue`s for any `decision.method is not EXACT` (the profiler
  abstains instead of claiming a sample/HLL-derived bool);
  `orchestration/rollup.py` gained `DatasetMetrics.key_facts`,
  `_full_relation_key_facts()` (ONE exact `count(DISTINCT col), count(col)` scan
  on `handle.view_name` for all columns), `_key_candidates(facts)` and
  `_apply_full_relation_uniqueness()` (patches an existing `uniqueness` block via
  `model_copy`, leaves `distinct_count` untouched so a sampled cardinality stays
  visibly `sampled`). Tests: +3 in `TestDerivedUniquenessIsFullRelation`
  (one-row sample cannot make a duplicated column unique; HLL at 20 000 rows
  cannot decide uniqueness; key candidates and the bool are one set), and the
  legacy `test_is_unique_candidate_still_derives_from_hll_value` rewritten to
  `…_is_not_derived_from_hll_value`. **947 passed / 277.87 s** full suite.
  Rebuilt (`pyinstaller --clean` → `ditto` install, exe sha256 `63257fbff18fba71`,
  13 schemas in bundle, packaged `--smoke` 6/6 incl.
  `PORTS_SMOKE_RESULT: only_pywebview_loopback` + `SMOKE_OK`;
  `.benchmarks/m7t5/smoke_installed_box7fix.log`). Packaged proof, new fixture
  `.benchmarks/m7t5/fixtures/run4_near.csv` (20 000 rows: `id` unique,
  `near_uniq` = i with rows 42+9000 both 5000 ⇒ 19 998 distinct, sha256
  `a9223cc5…`, unchanged after both runs — box 12):
  (a) **sampled** `job_e5f5015a3cc2` / `run d6cf005a…` (window 5000, seed 424242,
  standard, rule `id not_null`) ⇒ 48 `exact` + 6 `sampled` + 1 `not_computed`,
  `run.sampling = {enabled, reservoir, 5000, 424242}`, UI `Sampling seed 424242`
  + `sampled 5,000` badges (`box7f_14/15`);
  (b) **no sampling** `job_a0daddd73dd4` / `run 0edba2e9…` (standard mode, same
  fixture) ⇒ 49 `exact` + 5 `approximate` + 1 `not_computed`, UI `≈ 20,234`
  badges (`box7f_20/21`) — the exact bug scenario, since the old rule derived
  `false` from `HLL 20 234 ≠ 20 000`.
  `box7_derived_probe.py` recomputes truth with independent DuckDB exact counts:
  **RESULT: PASS on both** — `id` true (was the false negative), `near_uniq`
  false (a 5 000-row sample alone would have said true: 5 000 distinct = 5 000
  non-null), `amount`/`status`/`event_date` false, and
  `primary_key_candidates == {is_unique_candidate: true} == ['id']`
  (`box7f_sampled_derived.log`, `box7f_nosample_derived.log`; ADR-015 walkers
  `box7f_sampled_probe.log` + `box7f_nosample_probe.log` both `RESULT: PASS`).
  GOTCHA (GUI): the packaged window comes up **800×600** (not the resized
  1120×740 of run 2), so every click coordinate must be re-derived from a fresh
  `winshot`; and the app header is NOT sticky — after any scroll the nav links
  are off-screen, so scroll up first, then click `Run`/`Progress`/`Report`.
  GOTCHA (shell): macOS `pgrep` has no `-c` (it prints a usage line and returns
  nothing) — count with `pgrep -f … | wc -l` (fixed in
  `rebuild_box7fix.sh`). GOTCHA (probe): `profile.json` is the
  `result_document()` envelope — column facts live under `doc["profile"]`, not
  at the top level.
  **Follow-up (UI, not gate-blocking):** the Report per-column detail prints
  `Unique candidate  yes|no` with no source hint, directly under
  `Distinct count ≈ 20,234` — the bool is now a full-relation fact but a reader
  cannot tell. Worth a one-line hint ("from a full-relation count") in
  `frontend/src/pages/Report.tsx:888` next time that file is touched.
  CLOSED 2026-10-26 (hint added under the Uniqueness block; see CURRENT
  POSITION).

  **Run 3, step 8 — box 11 packaged worker-crash GREEN (2026-10-26, this
  session).** No code change — pure gate execution on the installed `.app`
  (`63257fbff18fba71`). Leg A: started `run2_big.csv` (2 M rows, rule
  `pk_id not_null`) → `job_3819909dcb44`; `box11_killwatch.py` SIGKILLed the
  spawn worker (pid 67356, `--multiprocessing-fork`) at step 4/14 → store
  mirrored `FAILED|WORKER_CRASHED` within ≤0.5 s; Progress screen showed the
  FAILED badge + `WORKER_CRASHED — see the §8 registry; partial results may
  exist (§6.4)` banner (`box11_37_progress_failed.png`) while the UI stayed
  responsive. Leg B: `box11_killhost.py` SIGKILLed the WHOLE host while
  `job_60ebe6e6173e` was RUNNING → store kept the open RUNNING row; on
  relaunch, the next `profile.start` (`job_c6f25d80cfa1`) triggered
  `recover_interrupted_jobs` → orphan flipped to `FAILED|WORKER_CRASHED`, and
  the new job itself SUCCEEDED 14/14 (run `5f4f6356…`, artifacts dir 8→9).
  Invariants after both legs: 0 orphan `runs` rows for the crashed jobs,
  `pragma integrity_check` + `foreign_key_check` ok, `run2_big.csv` sha256
  unchanged (box 12 re-proven), no stray worker processes. Verdict recorded
  in the board row 11 + CURRENT POSITION. GOTCHA (worker id): the spawn child
  to kill is the app-pid child whose argv contains `--multiprocessing-fork`;
  the sibling `-c from multiprocessing.resource_tracker import main` child is
  the resource tracker — killing it changes nothing (first attempt hit it and
  the job kept running).
  GOTCHA (GUI driving): native `<select>` popups render OUTSIDE the window
  bounds (winshot misses them) and a click at the screenshot position lands
  wrong after any scroll — open the popup, then commit with Down+Return; and
  cancelling the Browse NSOpenPanel with Escape leaves `browsing` stuck true
  (Browse shows "Opening…" forever; the promise never settles) — a real small
  frontend bug, non-gate, logged for the next Report/Dataset UI touch.
  CLOSED 2026-10-26: 15 s watchdog + `browseEpoch` late-settle guard in
  `DatasetSelection.handleBrowse`; root cause is pywebview 6.2.1/Cocoa not
  settling the js_api promise on dialog cancel — do NOT re-litigate, the
  frontend now tolerates it.
  Tools kept: `.benchmarks/m7t5/box11_gui.py` (focus-safe, re-derives window
  origin per action — RustDesk steals focus + the window hops displays),
  `box11_killwatch.py`, `box11_killhost.py`.

## Legend

`[ ]` todo · `[~]` in-progress (write session note in CURRENT POSITION) · `[x]` done (commit msg = task id) · `⛔` blocked (state why + what unblocks)

*Post-MVP milestones (Phase 4 / Final App) are intentionally NOT decomposed here — decompose them in a new section only after GATE MVP passes. Until then, BLUEPRINT §3.2 is the only scope reference.*
