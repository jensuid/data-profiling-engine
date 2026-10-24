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
Milestone : M2 — Engine CLI (spine; no UI)   (GATE M1 CONTRACTS FREEZE PASSED
          2026-10-24: M1-T1..T6 green; 9 result + 4 envelope schemas frozen,
          pydantic + TS mirrors in parity, §8 registry formalised)
Task      : none in progress — M2-T4 done, start M2-T5 next.
NEXT      : M2-T5 — Profilers: uniqueness + categorical (+ reference tests;
            HLL provenance). Open docs/ENGINE_SPEC.md §3 (Uniqueness +
            Categorical rows) + §2.2 (HLL / sampling) and
            docs/TESTING_BENCHMARKS.md §3.1. M2-T4 landed the runner pattern in
            engine/metrics/profiler.py (execute_stat_and_missingness) — add a
            second family pass (same module or sibling uniqueness.py /
            categorical.py) consuming the ExecutionPlan decisions the M2-T3
            registry already emits for uniqueness.{distinct_count (HLL approx
            template, expensive_exact → approximate at >LARGE_ROW_LIMIT or
            quick, algorithm duckdb HLL), is_unique_candidate (distinct==
            non-null), duplicate_groups} → ColumnProfile.uniqueness
            (MetricValue distinct_count + is_unique_candidate bool) and
            categorical.{frequencies,rare_categories,label_inconsistencies}
            (all sql_template=None → the profiler BUILDS the GROUP BY SQL; §4
            gate-1 low-cardinality trigger reads uniqueness.distinct_count)
            → ColumnProfile.categorical. Reuse the _not_computed_value /
            _metric_value honesty helpers + §6.4 per-task error policy +
            render_sql(params=…). Reference-test distinct (exact vs HLL within
            error bounds + labeled approximate) and frequencies vs
            Polars/hand. NOTE: the M2-T4 runner SKIPS non-{statistical,
            missingness} families by design (EXECUTED_FAMILIES); M2-T5 EXECUTES
            the families it owns.
            Test:  .venv/bin/python -m pytest data_profiling_engine/tests -q
Blocked by: none (M2-T1..T4 accepted).
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
