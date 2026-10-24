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
Milestone : M1 — Contracts Freeze   (GATE M0 PASSED 2026-10-24: M0-T1..T5
          green on BOTH OS; Win smoke = smoke_win.txt at repo root)
Task      : none in progress — M1-T4 done, start M1-T5 next.
NEXT      : M1-T5 — TypeScript types mirroring the contracts + a parity test
            against the JSON Schemas. Open docs/CONTRACTS.md §1 (parity rule)
            + AGENTS §3; mirror `data_profiling_engine/schemas/models.py`
            field-for-field into frontend/src (e.g. `types/contracts.ts`), then
            assert parity (property/enum sets vs `schemas/*.schema.json`) —
            extend tests/test_models.py-style parity checks or a node-side
            script; keep HashRouter/build green (`npm --prefix frontend run build`).
            Then M1-T6 (error-code registry + bridge/job envelopes).
Blocked by: none.
Session notes: M1-T4 delivered `schemas/models.py` (17 closed enums +
               ContractModel base + all objects for the 9 frozen schemas) and
               tests/test_models.py — 49 tests: dict→model→dump→`schemas.validate`
               round-trip for all 9 schemas, model-side conditional negatives
               (§1.1/§2/§4.1/§4.3/§9), field-name + required-set + enum parity
               vs canonical schemas, strict-vs-open additionalProperties
               behaviour. 162/162 pytest green 2026-10-24.
               Test:  .venv/bin/python -m pytest data_profiling_engine/tests -q
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
- Bridge spike introduced code `BRIDGE_REQUEST_INVALID` (see bridge/errors.py) not yet in
  CONTRACTS §8; M1-T6 promotes it into the formal error-code registry module + doc row.
  Same promotion applies to `JOB_NOT_FOUND` (added M0-T3).
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
- [ ] **M1-T5** TS types mirroring contracts + parity test against JSON Schemas. *Spec: CONTRACTS §1; AGENTS §3.*
- [ ] **M1-T6** Error-code registry module + bridge/job envelope models. *Spec: CONTRACTS §5.3, §8.*

## M2 — Engine CLI (spine; no UI)

- [ ] **M2-T1** Connectors: CSV + Parquet → DuckDB relation, read-only, `content_hash`, row-id policy. *Spec: ENGINE §5.2 note; CONTRACTS §2.*
- [ ] **M2-T2** Discovery: physical→logical/semantic type inference + unit status. *Spec: ENGINE §4.1.*
- [ ] **M2-T3** Metric registry core + planner cost classes. *Spec: ENGINE §2, §2.1.*
- [ ] **M2-T4** Profilers: statistical + missingness (+ reference tests). *Spec: ENGINE §3; TESTING §3.1.*
- [ ] **M2-T5** Profilers: uniqueness + categorical (+ reference tests; HLL provenance). *Spec: ENGINE §3, §2.2.*
- [ ] **M2-T6** Profilers: distribution (histogram, outliers) + temporal single-column. *Spec: ENGINE §3.*
- [ ] **M2-T7** Rule registry + compiler → one SQL per rule; statuses PASS/FAIL/WARN/ERROR. *Spec: ENGINE §5.1–5.3; CONTRACTS §4.1.*
- [ ] **M2-T8** Findings: detectors, dedup/merge, prioritization, **fingerprint**. *Spec: ENGINE §6; CONTRACTS §4.4.*
- [ ] **M2-T9** Recommendations: rule-based templates + narrative w/ evidence refs. *Spec: ENGINE §7; CONTRACTS §4.3.*
- [ ] **M2-T10** Modes quick/standard/deep + sampling (reservoir, seed) wiring. *Spec: ENGINE §2.2, §8.*
- [ ] **M2-T11** `profiler-cli` end-to-end on `sales_deals` fixture → full contract-valid JSON. **→ GATE M2 (BLUEPRINT Phase 1).** *Spec: CONTRACTS §2–4; TESTING §2.*

## M3 — Persistence

- [ ] **M3-T1** SQLite metadata schema (runs/jobs/registry/checks/findings refs) + migrations. *Spec: ADR-008; SECURITY §2, §6.5.*
- [ ] **M3-T2** Artifact store: layout, lifecycle policy, cleanup. *Spec: SECURITY §2.*
- [ ] **M3-T3** Normalized metric persistence `(run_id, column, metric)` for cheap diffs. *Spec: ADR-008; ENGINE §9.*

## M4 — Reporting Layer

- [ ] **M4-T1** JSON result writer from contracts (golden snapshots). *Spec: ENGINE §10; TESTING §2.*
- [ ] **M4-T2** HTML self-contained report, inline Plotly, method badges, masking default. *Spec: ENGINE §10; SECURITY §4.*
- [ ] **M4-T3** CSV issue export columns + artifact refs. *Spec: ENGINE §10.*
- [ ] **M4-T4** Language decision (BLUEPRINT Q1) recorded here, then templates. *Spec: BLUEPRINT §8.*

## M5 — Bridge Facade + Job Manager (full)

- [ ] **M5-T1** Command handlers for MVP command set; payload caps enforced. *Spec: CONTRACTS §5.2, §5.4.*
- [ ] **M5-T2** Job manager: lifecycle, cancel, timeout, crash detection, recovery. *Spec: CONTRACTS §6; ADR-006.*
- [ ] **M5-T3** Progress polling + optional evaluate_js push, one status schema. *Spec: CONTRACTS §5.5.*

## M6 — UI Screens (one task = one screen)

- [ ] **M6-T1** Dataset Selection + inspect/preview. *Spec: BLUEPRINT Phase 2; CONTRACTS §5.*
- [ ] **M6-T2** Run Configuration (mode, sampling, config upload w/ validation errors). *Spec: CONTRACTS §9.*
- [ ] **M6-T3** Run Progress (status/percent/cancel). *Spec: CONTRACTS §6.*
- [ ] **M6-T4** Report View: Overview + Column Profiling. *Spec: expected-output §3.1–3.2.*
- [ ] **M6-T5** Report View: Visualizations + Quality Assessment. *Spec: expected-output §3.3–3.4.*
- [ ] **M6-T6** Report View: Findings & Recommendations + Export actions. *Spec: expected-output §3.4–3.5.*

## M7 — Hardening & Release Gate

- [ ] **M7-T1** Benchmark harness + synthetic generator; baseline numbers recorded into TESTING §4.3. *Spec: TESTING §4.*
- [ ] **M7-T2** Scale guarantees verified (RSS flatness, cancel latency, recovery). *Spec: TESTING §4.3.*
- [ ] **M7-T3** Both-OS installer matrix: install→job→export→upgrade→uninstall. *Spec: SECURITY §6; TESTING §5.1.*
- [ ] **M7-T4** Security acceptance automation. *Spec: SECURITY §8; TESTING §5.4.*
- [ ] **M7-T5** **→ GATE MVP: every box of TESTING §5.1 green on packaged builds.**

## Legend

`[ ]` todo · `[~]` in-progress (write session note in CURRENT POSITION) · `[x]` done (commit msg = task id) · `⛔` blocked (state why + what unblocks)

*Post-MVP milestones (Phase 4 / Final App) are intentionally NOT decomposed here — decompose them in a new section only after GATE MVP passes. Until then, BLUEPRINT §3.2 is the only scope reference.*
