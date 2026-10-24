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
Task      : none in progress — M2-T9 done, start M2-T10 next.
NEXT      : M2-T10 — Modes quick/standard/deep + sampling (reservoir, seed)
            wiring. Open docs/ENGINE_SPEC.md §2.2 + §8 (mode → section gating +
            method defaults; reservoir sampling w/ a fixed seed) and the M2-T4
            note here (the profiler already runs ONE shared bounded sample
            relation via md5('seed#‖_row_id') ORDER+LIMIT — the reservoir is
            finalised HERE, not there). Verify the planner's §2.1×§8
            decide_method already honours quick/standard/deep + sampling seed
            (M2-T3/T4); M2-T10 wires the *end-to-end* mode/sampling inputs
            through the engine CLI path so a sampled run stamps seed on every
            sampled metric (ADR-015) and the reservoir is a real bounded
            sample, not the ORDER+LIMIT placeholder. Do NOT add new metrics.
            Test:  .venv/bin/python -m pytest data_profiling_engine/tests -q
Blocked by: none (M2-T1..T9 accepted).
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
