---
title: "Testing & Benchmarks — Data Profiling Engine"
aliases: [Testing, Acceptance, Benchmarks]
tags: [testing, acceptance, benchmark, quality]
status: active
created: 2026-10-24
doc_role: supporting
---

# Testing Strategy, Acceptance Criteria & Performance Benchmarks

> [!IMPORTANT] No "done" without this document
> A feature is complete only when its acceptance criteria here pass **on the packaged build for both OS**, not in dev mode. Because the engine is home-grown (ADR-012), statistical correctness tests (§3.1) are non-negotiable.

## 1. Test pyramid

| Layer | Scope | Runs |
|---|---|---|
| Unit | profilers, rules, inference, findings, normalization, config validation, job transitions | every commit |
| Contract | every [[CONTRACTS]] object validates against JSON Schema; TS↔Py type parity | CI |
| Integration | bridge↔host, host↔worker, worker↔DuckDB, SQLite persistence, artifact lifecycle, cancel/timeout, error propagation | CI |
| Desktop / E2E | app startup, file dialogs, HashRouter over `file://`, bridge invocation, UI responsiveness during job, install/upgrade/uninstall, recovery | both OS, per release |
| Benchmark | scale, memory, cancellation, recovery | scheduled + release gate |

## 2. Fixtures & golden data

- **Small deterministic fixtures** for correctness (hand-checkable): e.g. 10-row numeric/categorical/temporal sets with known mean, median, missing rate, distinct count, quantiles.
- **`sales_deals` simulation fixture** mirroring the expected-output reference (20k rows, 8 cols, injected issues: 12 duplicate `deal_id`, 15% missing `deal_value`, 186 `Closed Won` missing `closed_at`, 1.2% missing `sales_rep_id`, 47 outlier candidates) as the end-to-end scenario with known expected findings.
- **Large synthetic generator** for benchmarks (see §4.1) — configurable rows/cols/cardinality/missingness/skew.
- **Golden result files**: canonical JSON outputs for fixtures, used to detect unintended metric drift (regression snapshots).

## 3. Correctness tests

### 3.1 Metric & rule reference tests (highest priority)

Every metric in the registry ([[ENGINE_SPEC]] §3) and every rule type (§5.1) has a test asserting the engine's value equals an **independent reference** within tolerance:

- Exact metrics: assert equality to Polars / plain-SQL / hand-computed value.
- Approximate metrics (HLL distinct, t-digest quantiles): assert **within documented error bounds** and that `method` is labeled `approximate`.
- Sampled metrics: assert reproduction with same `seed` (deterministic), and that changing seed changes the estimate but stays within tolerance of the true value.
- Edge cases: all-null column, single value, empty dataset, unicode, mixed-type CSV column coerced, integer vs float, timezone-naive vs aware timestamps, sentinel/blank vs null.

### 3.2 Status semantics tests

- A rule that cannot execute → `ERROR`, **not** `FAIL` (e.g. `greater_than_or_equal` on a text column).
- `WARN` rules never produce hard failure; `FAIL` requires successful execution + violation.
- Per-task error does not abort other tasks (partial-failure policy, [[CONTRACTS]] §6.4).

### 3.3 Provenance & reproducibility tests

- Every numeric result carries `method` (+ `seed` when sampled).
- Re-running identical input+config+engine version yields byte-identical `content_hash`, `config_hash`, and equal metrics (golden).
- `not_computed` always carries a `reason` (no silent null).

### 3.4 Finding/dedup/fingerprint tests

- Same issue, different run, different values → **same `fingerprint`**; different issue → different fingerprint (ADR-016).
- Detector merge produces no duplicate alerts for one root cause.
- No finding places a hypothesis in `facts`; no recommendation asserts an unverified cause (epistemic checks, [[CONTRACTS]] §7) — enforced by schema validation + content assertions in the golden fixture.

### 3.5 Contract validation tests

- Invalid dataset config (unknown rule key, bad type) rejected **before** profiling with `CONFIG_INVALID`.
- Rule missing `dimension` → config invalid (scorecard dependency).

## 4. Performance & benchmark contract

Architecture success is measured against realistic workload, not "the app opens".

### 4.1 Workload matrix (generate via synthetic harness)

Vary: rows (1M / 10M / 50M / 100M), columns (8 / 50 / 500), file size, cardinality (low/high), missingness (0–40%), distinct values, distribution (skewed/normal), number of quality rules (7 / 50 / 200), query complexity, source (CSV vs Parquet), concurrent jobs, cancel during run.

### 4.2 Metrics captured

| Metric | Definition |
|---|---|
| Correctness | results match reference at scale (spot-checked) |
| Wall-clock time | total run + per-phase (profile/quality/findings/report) |
| Peak memory (RSS) | of worker; must stay flat vs rows for streaming metrics (no full-materialization) |
| Disk usage | staging + cache footprint |
| Source query cost | (Phase 2) rows/bytes read from DB |
| UI responsiveness | main thread stays interactive during job (input latency sample) |
| Cancellation latency | time from `profile.cancel` → `CANCELLED` |
| Recovery | worker crash leaves metadata consistent, no corruption |
| Reproducibility | identical rerun matches |
| Bridge payload size | max message under 1 MiB cap during realistic browsing |

### 4.3 Targets

- **No universal time limit is set before baseline benchmarks** (per reference guidance). Phase 0–1 runs establish machine/dataset-representative baselines; targets are then recorded here as an explicit table.
- **Structural guarantees that ARE fixed now (binding, not numeric):**
  - A standard profile of a large CSV/Parquet must **not** load the full dataset into Python RAM — peak worker memory stays bounded as rows grow (DuckDB streaming/spill). Verified by comparing 10M vs 100M RSS curves.
  - Sampled/approximate paths must be **strictly cheaper** in wall-clock than exact on the same large fixture, proving the planner degrade actually works.
  - Cancellation must complete within the worker's per-step boundary (bounded), not only at job end.

### 4.4 Regression tracking

Benchmarks are versioned; a run failing an RSS-bound or sampling-cost guarantee blocks merge. Results are documented (numbers + hardware) as product evidence.

## 5. Acceptance criteria by milestone

### 5.1 MVP acceptance (release gate)

All must pass on packaged macOS **and** Windows:

- [ ] Load a dataset via a supported connector (CSV **and** Parquet; no other connector reachable) — ADR-011.
- [ ] App shows dataset summary + schema profile; physical vs logical types distinguished.
- [ ] Engine produces statistics + distributions + missingness + uniqueness.
- [ ] Quality rules run; `PASS`/`FAIL`/`WARN`/`ERROR` distinguished correctly.
- [ ] Findings carry evidence + refs to metric/rule; `fingerprint` present.
- [ ] Recommendations do not assert unproven causes.
- [ ] Every metric labeled `exact`/`approximate`/`sampled`; sampled marked + seed recorded; no sample claimed as exact — ADR-015.
- [ ] HTML report + JSON result generated from the same structured result; CSV issue export present; HTML self-contained (offline charts).
- [ ] Run has `run_id`, config version/hash, status, duration, error log, engine version.
- [ ] Profiling runs in the **worker**, not the UI thread; UI stays responsive; job observable + cancellable.
- [ ] Worker crash does not corrupt the metadata store; recovery on restart works.
- [ ] Source files are **not modified** by profiling.
- [ ] Internal comms use the bridge only — **no HTTP/TCP port bound** — ADR-005; SPA routing works over local files (HashRouter) — ADR-013.
- [ ] No local dev server required for the packaged run.
- [ ] Unit + integration tests (§3) green; `sales_deals` fixture yields the known findings.
- [ ] Installer: clean install, launch, one full job, export, upgrade (data preserved), uninstall (data preserved) on both OS.
- [ ] Benchmarks documented with recorded baselines.

### 5.2 Phase-0 spike gate (pre-build)

- [ ] pywebview + React/HashRouter + PyInstaller build launches on both OS.
- [ ] Bridge round-trip to a `multiprocessing` worker (spawn) completes in the packaged build, incl. on Windows.
- [ ] DuckDB query runs inside the packaged binary (native lib collected).
- **Fail → revise ADR-003/009/010/013 before Phase 1.**

### 5.3 Final App acceptance (additive, Phase 4)

- [ ] Baseline/drift diff keyed by `fingerprint` + metric identity; method changes labeled as methodology (not quality) drift.
- [ ] Per-dimension quality score with declared weights + rule-level evidence; no single opaque score by default; accuracy not inferred from validity.
- [ ] Interactive explorer: column detail, segment comparisons, problem-record samples (masked per policy).
- [ ] Findings workspace: owner/status/comments/resolution evidence using `FindingWorkflow` contract.
- [ ] Run history + (optional) scheduling/monitoring.
- [ ] Excel + one SQL connector (pushdown, read-only, native credential store) added without changing base result contracts.
- [ ] (Optional) AI summary cites existing evidence only; never fabricates numbers/causes.

### 5.4 Security acceptance

See [[SECURITY_PACKAGING]] §8 — verified as automated tests here: no bound port; bridge rejects unknown/invalid/out-of-scope/oversized; PII masked by default; credentials absent from logs/results; upgrade/uninstall data policy honored.

## 6. Test tooling notes

- Python: `pytest`; property-based tests for metric invariants (e.g. missing_rate ∈ [0,1]; distinct ≤ row_count).
- DuckDB: in-memory/file fixtures; determinism enforced via fixed `seed`.
- Contracts: `jsonschema` validation + golden snapshot diffs.
- Frontend: `vitest`/`playwright` for HashRouter routing over `file://` and bridge stubs.
- Packaging smoke: launch packaged binary in CI per OS runner (Windows runner mandatory, per BLUEPRINT Q4).
