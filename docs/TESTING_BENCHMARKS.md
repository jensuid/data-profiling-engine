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
  - A standard profile of a large CSV/Parquet must **not** load the full dataset into Python RAM — peak worker memory stays bounded as rows grow (DuckDB streaming/spill). Verified by comparing 10M vs 100M RSS curves. Since M7-T2c the same series is **also** reported in a stricter form as a diagnostic — `evaluate_rss_plateau` fits RSS ∝ rows^b and calls `plateau` only when **b ≤ 0.25** *and* the largest run stayed inside the engine's ADR-021 memory ceiling (`growing` / `over_ceiling` otherwise). The binding gate remains `evaluate_rss_boundedness`; the reason is recorded in §4.3.4 — our own measured series grows as rows^0.77, a ceiling only flattens the region **above** its value, and a gate that today cannot be passed is a false alarm, not evidence.
  - Sampled/approximate paths must be **strictly cheaper** in wall-clock than exact on the same large fixture, proving the planner degrade actually works.
  - Cancellation must complete within the worker's per-step boundary (bounded), not only at job end.

### 4.3.1 Recorded baseline (M7-T1, dev-mode)

Generated + measured by `data_profiling_engine/benchmark` (`python -m
data_profiling_engine.benchmark baseline …`); machine-readable copy at
`data_profiling_engine/benchmark/baseline_macos_dev.json`. **Dev-mode** (engine
spine via `profile_dataset`), **not** the packaged build — the packaged
both-OS baseline is still owed at the release gate (§5.1 / BLUEPRINT Phase 3).

Machine: macOS 13.7.8 (x86_64, kernel 22.6.0), 4 cores, 16 GB RAM, Python 3.12.4,
DuckDB streaming with full-relation quality rules. Peak RSS is a per-run child
high-water mark (`resource.getrusage`), so each number is isolated.

Workload: CSV, 8 cols, **high** cardinality, 12% missingness, **skewed** numeric,
seed 1337; reservoir sampling window 200 000 rows for the degrade pair.

| Case | Rows | Mode / method | Wall-clock | Peak RSS | Source |
|---|---:|---|---:|---:|---:|
| std  | 200 000 | standard (exact/approx) | 62.9 s | 94.0 MB | 11.7 MB |
| std  | 500 000 | standard (exact/approx) | 92.5 s | 131.2 MB | 30.0 MB |
| std  | 1 000 000 | standard (exact/approx) | 143.7 s | 169.5 MB | 60.3 MB |
| exact | 1 000 000 | **deep** (no sampling) | 157.5 s | 169.7 MB | 60.3 MB |
| degrade | 1 000 000 | standard + **sampled** window | 144.7 s | 188.8 MB | 60.3 MB |

**Structural-guarantee verdicts (binding, §4.3):**

- **RSS bounded as rows grow — PASS.** 200k→1M is ×5 rows but only ×1.80 peak
  RSS (94→169 MB): sub-linear, so the pipeline does **not** materialise the
  relation in Python RAM (DuckDB streams/spills). All runs `SUCCEEDED`, 0 errors.
- **Degrade strictly cheaper than exact — PASS (marginal).** sampled 144.7 s <
  exact 157.5 s (−12.8 s, ×1.09). The margin is thin and *honest*: wall-clock here
  is dominated by (a) the per-column×metric query count and (b) quality rules that
  run on the **full relation regardless of mode** (ENGINE §8); sampling degrades
  only the heavy distinct/sketch slice, so the saving grows with that slice's
  share (larger-cardinality / more-distinct workloads widen it). Verdict is the
  required *strict* inequality, so the guarantee holds.

Deferred to the release gate (harness already drives them via one command):
the 10M–100M RSS-curve ladder (§4.3 structural bullet 1) — **since measured, see
§4.3.3** — the 500-col and
0/40%-missingness corners of §4.1, cancellation latency, crash-recovery, Parquet
sources, and the **packaged** macOS + Windows baseline (§5.1).

### 4.3.2 Cancellation + crash recovery (M7-T2b, dev-mode)

The two §4.2 rows the RSS/degrade baseline in §4.3.1 cannot reach —
**Cancellation latency** and **Recovery** — are measured by
`data_profiling_engine/benchmark/cancel_recovery.py`, driven through the real
store-backed `JobManager.start_profile_job` path (ADR-006, `multiprocessing`
**spawn**). Release-gate command:

```
python -m data_profiling_engine.benchmark guarantees [--rows 5000] \
    --workdir .benchmarks/guarantees --out .benchmarks/guarantees/results.json
```

Structural verdicts (binding, no numeric wall-clock target per §4.3 "No
universal time limit is set before baseline benchmarks"):

- **Bounded cancellation** — verdict `bounded` iff the RUNNING worker reaches
  `CANCELLED` with `steps_completed < PROFILING_TOTAL_STEPS (6)`, i.e. the
  flag is observed at a step boundary, not only at job end. The
  `latency_s` field records the wall time from `cancel_job(…)` to the
  terminal transition; on MVP it is bounded by the still-executing DuckDB
  step because `profile_job.py` cannot interrupt an active query (§6.5
  Phase-3 depth item). A `at_job_end` verdict is a regression signal.
- **Consistent recovery** — verdict `consistent` iff (a) the live manager
  surfaces `FAILED` + `WORKER_CRASHED` on a hard-killed worker (SIGKILL,
  not `terminate()`, so the graceful path is *not* what is exercised), (b)
  the SQLite mirror agrees on the same state + code, (c) no orphan `runs`
  row exists (persistence is the worker's last step, never reached on a
  crash), and (d) a fresh `JobManager` on the same store reconciles zero
  unfinished jobs — the live path already mirrored. Any broken invariant
  names itself in the verdict's `problems` list.

Dev-mode by design: `multiprocessing` spawn semantics + on-disk SQLite
behave identically in the packaged build, so the same command runs on the
macOS + Windows release-gate (§5.1) with no code path change. The two
cases are pinned as pytest integration tests in
`tests/test_benchmark_cancel_recovery.py` (~12s each on a 5 000-row
generated fixture; the fixture is sized so the worker is still in
`profile_dataset` when the cancel/kill lands — a smaller fixture races
and returns the honest `not_cancelled` / `not_killed` inconclusive
verdicts, never a false pass).

Initial dev-mode verdicts (macOS 13.7.8 x86_64, 4 cores, 16 GB RAM, Py 3.12.4,
CSV 5 000 rows / 5 cols / medium cardinality / 10% missingness / normal
distribution / seed 7; command above, exit 0):

| Guarantee | Verdict | Measured |
|---|---|---:|
| Cancellation bounded (§4.3 binding) | **bounded** | 5.48 s cancel→CANCELLED, stopped at step 2/6 |
| Worker-crash recovery consistent (§4.2) | **consistent** | 0.055 s SIGKILL→crash-detected, store mirrored FAILED / WORKER_CRASHED, no orphan run row |

### 4.3.3 RSS-curve ladder 10M → 100M (M7-T2a, dev-mode release-gate run)

The scheduled run behind §4.3 structural bullet 1 ("Verified by comparing 10M vs
100M RSS curves"), executed with the M7-T1 harness:

```
python -m data_profiling_engine.benchmark baseline --sizes 10000000,100000000 \
    --columns 8 --format csv --cardinality high --missingness 0.12 \
    --distribution skewed --no-degrade \
    --workdir .benchmarks/ladder --out .benchmarks/ladder/results.json
```

Machine: macOS 13.7.8 (x86_64, kernel 22.6.0), 4 cores, 16 GB RAM, Python 3.12.4.
Workload knobs are **identical to the §4.3.1 baseline** (high cardinality, 12 %
missingness, skewed numeric, seed 42) so the two series join into one curve.
Scope decisions taken at execution time (the task delegates them): the 50M tier
was skipped — the verdict needs ≥ 2 measured points and the spec names 10M vs
100M — and the exact-vs-sampled pair was **not** re-run (`--no-degrade`), since
§4.3.1 already records that guarantee and a 100M pair would add two more full
passes. Machine-readable copy: `data_profiling_engine/benchmark/ladder_10m_100m_macos_dev.json`.
Progress during the run is observable via the read-only `benchmark watch`
subcommand (time-estimate percentages reconstructed from the harness's own
on-disk stage chain; it is a monitor, not product evidence).

| Tier | Rows | Mode / method | Wall-clock | Peak RSS | Source |
|---|---:|---|---:|---:|---:|
| ladder | 10 000 000 | standard (exact/approx) | 1 310.2 s | 677.0 MB | 612.4 MB |
| ladder | 100 000 000 | standard (exact/approx) | 9 843.7 s | 3 948.2 MB | 6 219.0 MB |

Both runs `SUCCEEDED` with 0 per-task errors. Wall-clock grew ×7.5 for ×10 rows
(sub-linear): 131.0 s per million rows at 10M vs 98.4 s per million at 100M.

**Structural-guarantee verdict (binding, §4.3):**

- **RSS bounded as rows grow — PASS.** `evaluate_rss_boundedness` →
  `rss_ratio 5.832 < row_ratio 10.0` → `bounded`. Nothing in the pipeline scales
  1:1 with the relation, and a Python-side full materialisation of the 6.2 GB
  source would have tracked `row_ratio` (≈ ×10 from the 10M point, ~6.8 GB+) and
  overflowed this 16 GB machine; it did not.

**Honest reading — bounded, but NOT flat (escalated, resolved in §4.3.4).** Joining
§4.3.1 into the full series 200 k → 1M → 10M → 100M gives peak RSS 94.0 → 169.5 →
677.0 → 3 948.2 MB, i.e. per-row-decade growth ×1.80 / ×4.00 / ×5.83 — a power
law, not a plateau. The first draft of this note summarised it as `rows^0.65`,
which **understated** the problem: fitting the 10M/100M pair properly
(`evaluate_rss_plateau`) gives **rows^0.766**, and that exponent extrapolated to
1B rows predicts ≈ 22.5 GiB on this box, i.e. OOM. The mechanism was DuckDB's own
buffer-pool sizing: the engine never set `memory_limit` (default ≈ 80 % of RAM —
measured 12.7 GiB here) nor `temp_directory` (default the relative `.tmp`, i.e.
resolved against the process CWD). Legitimate streaming, but not the "flat" the
§4.2 metric row ("must stay flat vs rows") aspires to.

Both halves of that follow-up are now done: **ADR-021** makes the memory and
spill ceilings engine policy applied by one connection helper, and **§4.3.4**
records the re-measure that proves the ceiling binds and what it costs. That
re-measure also explains why a plateau is still **not** proven: at 10M rows peak
RSS *is* the SQL working set (~630–680 MB), which sits **below** the ceiling, so
the ceiling never becomes the binding constraint there. The top of the ladder was
then re-measured under the shipped default (`bounded_100m_top`): **3 724.0 MiB**
(90.9 % of the 4 GiB ceiling, 87.7 MiB spilled, 0 errors, 8 847.8 s) — −5.7 % RSS
vs the unbounded run and still `rows^0.74`, confirming the ceiling is *reached*
but not yet *decisive* at 100M. Still owed from this ladder: a pass with the
ceiling forced **below** the 100M working set (the only direct plateau
demonstration) and the whole series on the **packaged build** (§5.1).

Still owed at the release gate: the 500-column and 0/40 %-missingness corners of
§4.1, Parquet sources, and the **packaged** macOS + Windows baseline (§5.1) —
the latter runs the same two commands (`baseline`, `guarantees`) and, on Windows,
degrades to wall-clock/disk only because POSIX `resource` has no peak-RSS field
(harness records `peak_rss_mb: null` there rather than guessing).

### 4.3.4 Owned DuckDB ceilings + bounded re-measure (M7-T2c, dev-mode)

**ADR-021** replaced every bare `duckdb.connect()` with
`duckdb_session.connect()`, which installs and then *reads back*
`memory_limit = min(4 GiB, 25 % of RAM)`, an owner-only `temp_directory` under
the OS temp root, and `max_temp_directory_size = 8 GiB`. The ceiling is floored
at `2 × worker_threads × 128 MiB` (1 GiB on this 4-core box) because DuckDB's
`median`/`quantile_cont` allocate a fixed 128 MiB buffer they cannot spill.

Measured with `.benchmarks/ladder/run_pass.sh` — the same retained 10M fixture
(`csv_10000000_std`, 612.4 MB, same §4.3.1 workload knobs) profiled under four
ceilings. The ceiling was overridden only via `DPE_DUCKDB_MEMORY_LIMIT`, so the
code path under test is the shipped one:

| Pass | `memory_limit` | Wall-clock | Peak RSS | Peak spill | Per-task errors |
|---|---|---:|---:|---:|---:|
| M7-T2a (no helper, DuckDB default) | 12.7 GiB (inherited) | 1 310.2 s | 677.0 MB | — | 0 |
| shipped default | 4 GiB | 1 218.8 s | 675.1 MiB (707 928 064 B) | none | 0 |
| shipped floor (4 cores) | 1 GiB | 1 251.2 s | 631.6 MiB (662 319 104 B) | none | 0 |
| forced tight | 256 MB | 914.0 s | 375.7 MiB (393 973 760 B) | 146.3 MiB | **28** |

All four runs are `SUCCEEDED`. Spill is measured by sampling the scratch directory
every 5 s (`spill_samples_<pass>.log`): DuckDB 2.0.0's `duckdb_memory()` counters
report *live* buffers only and read `0` once a query finishes, so they cannot
witness a spill after the fact. Machine-readable copy:
`data_profiling_engine/benchmark/bounded_ceilings_macos_dev.json`.

**The top of the ladder under the shipped default (100M rows, same fixture, retained):**

| Pass | `memory_limit` | Wall-clock | Peak RSS | Peak spill | Errors |
|---|---|---:|---:|---:|---:|
| M7-T2a (no helper) | 12.7 GiB (inherited) | 9 843.7 s | 3 948.2 MB | — | 0 |
| shipped default | 4 GiB | **8 847.8 s** | **3 724.0 MiB** (3 904 925 696 B) | **87.7 MiB** | 0 |

Against the unbounded run: 100M peak RSS **−5.7 %**, wall **−10.1 %**, 0 errors.
The ceiling reached **90.9 % of its own value** (3 724 of 4 096 MiB) and the engine
spilled for the first time on this tier (**87.7 MiB peak**, sampled in the pass's
own scratch dir). The −10.1 % wall-clock is a **single run per configuration** and
is not attributed to the ceiling — it is within plausible page-cache variance.

**Honest reading — what this proves and what it does not:**

1. **The ceiling is installed and it binds.** The 256 MB pass spilled 146 MiB to
disk and held RSS at 375.7 MiB, i.e. below the working set — the engine now
spills instead of growing (ADR-007's assumption made real).
2. **The ceiling does NOT shrink a run whose working set is under it.** 4 GiB →
   675.1 MiB and 1 GiB → 631.6 MiB against 677.0 MiB unbounded: a −0.3 % / −6.7 %
   spread that is run-to-run variance, not an improvement. At 10M rows peak RSS
   is set by the SQL working set (≈630–680 MB), so the ceiling is a **safety
   boundary above that working set**, never a knob for making a profile smaller.
   Any claim that ADR-021 "reduced memory" would be false; it bounds it.
3. **A ceiling under the working set trades coverage for bytes.** At 256 MB
   every distribution/median metric became an `ERROR`
   (`OutOfMemoryException: failed to allocate data of size 128.0 MiB`) — 28
   per-task errors, run still `SUCCEEDED`, no wrong numbers (FAIL ≠ ERROR and
   ADR-015 provenance held). Hence the derived floor: below it the profile does
   not get smaller, it loses metrics.
4. **The tight pass is the fastest run only because it skipped work** (914 s vs
   ≈1 220–1 310 s). Wall-clock is not comparable across these four passes; only
   the three ceiling-above-working-set passes are.
5. **A plateau is still not proven — now measured, not extrapolated.** The
   curve's shape barely moved: the 10M→100M exponent is **0.742** under the
   ceiling vs **0.766** unbounded, and fitting the whole 200k→100M series gives
   **0.599** vs 0.607 (×3.97 vs ×4.05 per decade, ≈12.4 vs 13.2 GiB at 1B rows).
   That is expected, not a failure: a ceiling can only flatten the region **above
   its own value**, and at 100M the working set (3 724 MiB) still fits inside the
   4 GiB default — it only *touches* the ceiling (90.9 %, 87.7 MiB spilled).
   Flattening would require a ceiling *below* the 100M working set (e.g. 1 GiB,
   which would pin RSS near 1 GiB at the cost of ~3 GB of spill). Until that pass
   exists, the binding gate stays `evaluate_rss_boundedness` + `SUCCEEDED` + 0
   errors and the plateau exponent is reported as a diagnostic.

Reference tests for the policy (pure arithmetic over a faked RAM size, read-back
verification, blank-env = unset, floor per thread count, a tight-ceiling
aggregation matched against a pure-Python reference, and a source-level guard
that fails on any new bare `duckdb.connect()`) live in
`tests/test_duckdb_session.py`.

**Still owed from this follow-up:** a **forced ceiling below the 100M working
set** (the only direct demonstration of flattening: pin RSS near 1 GiB at 100M
and accept the spill), a 100M re-run to turn the −10.1 % wall-clock into a
defensible number, and the whole series on the **packaged build** (§5.1).

**GOTCHA (monitor, found here):** the pass *records* the workspace it used in its
`ceilings.temp_directory`. A progress watcher that reads its own process default
instead reported "0 MiB spilled" for the very pass that had spilled 87.7 MiB —
read the recorded value, never assume the default. `benchmark watch --pass-name`
now does that.

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

Automated as of M7-T4 in `data_profiling_engine/tests/test_security_acceptance.py` (one test class per §8 bullet; the packaged `--smoke` run additionally self-checks "no bound port" via the `PORTS_SMOKE_RESULT` marker — see the installer runbooks).

## 6. Test tooling notes

- Python: `pytest`; property-based tests for metric invariants (e.g. missing_rate ∈ [0,1]; distinct ≤ row_count).
- DuckDB: in-memory/file fixtures; determinism enforced via fixed `seed`.
- Contracts: `jsonschema` validation + golden snapshot diffs.
- Frontend: `vitest`/`playwright` for HashRouter routing over `file://` and bridge stubs.
- Packaging smoke: launch packaged binary in CI per OS runner (Windows runner mandatory, per BLUEPRINT Q4).
