---
title: "AGENTS — Coding Agent Instructions"
aliases: [AGENTS, Agent Rules]
tags: [agents, conventions, guardrails]
status: active
created: 2026-10-24
doc_role: agent-guide
---

# AGENTS.md — Working Rules for the Coding Agent

You are implementing the **Data Profiling Engine**, a desktop (macOS + Windows) profiling & quality engine. Follow the specification pack; **do not make fundamental architecture decisions yourself.**

## 1. Source of truth (precedence)

Apply in this order; higher wins on conflict:

0. `STATE.md` (repo root) — **working memory**: current milestone, task status, next action, gotchas. Read it FIRST every session. It is a status log, not a spec; if it contradicts docs 1–5, the docs win and STATE.md must be corrected.
1. `docs/BLUEPRINT.md` — scope, milestones, **locked decisions C1–C4**.
2. `docs/ARCHITECTURE_AND_ADRS.md` — structure + ADRs (ADR-001…016).
3. `docs/CONTRACTS.md` — the only definition of cross-boundary object shapes.
4. `docs/ENGINE_SPEC.md` — computation behavior.
5. `docs/SECURITY_PACKAGING.md`, `docs/TESTING_BENCHMARKS.md`.
6. `references/*` — **frozen inputs, not maintained specs.** If they contradict docs 1–5, the docs win (ADR-014).

When you need a fact, look it up in the owning doc. **Do not restate or invent** a contract/decision in code comments that contradicts it.

## 2. Hard architectural rules (never violate)

- **No HTTP/TCP server, ever** — not FastAPI, not localhost polling, not a dev server in the packaged app. Frontend↔backend only via the pywebview bridge facade (ADR-005). Remote DB/warehouse network connections are the only allowed outbound network.
- **Frontend uses HashRouter** (ADR-013). No browser-history routing. Production loads local `file://` assets (ADR-010).
- **MVP connectors: CSV + Parquet only** (ADR-011). Do not add Excel, SQL, or API connectors in MVP even if "easy".
- **Own DuckDB-SQL engine. No YData, no Great Expectations, no Soda dependencies** (ADR-012). GX-style YAML is a *syntax* choice only.
- **Heavy work runs in a separate worker process, never the UI thread** (ADR-006). `multiprocessing` with spawn semantics; `freeze_support()` set.
- **Never load a full large dataset into Python RAM** for profiling — compute via DuckDB SQL; materialize only bounded samples/aggregates/pages (ADR-007).
- **Read-only against sources by default**; never modify or delete source data. No silent auto-remediation.

## 3. Contract obligations

- Implement result objects exactly as `CONTRACTS.md` defines; keep Python (pydantic) and TypeScript types in parity, validated against `schemas/` JSON Schemas.
- **Every numeric metric/check carries `method` ∈ `{exact, approximate, sampled, not_computed}` + `method_params`; sampled REQUIRES a `seed`** (ADR-015). Never present a sampled/approximate value as exact.
- **Every Finding carries a deterministic `fingerprint`** (ADR-016). Absent values are `not_computed` + `reason`, never silent null.
- `FAIL` ≠ `ERROR`: an un-executable check is `ERROR`, not a violation. Preserve per-task errors without aborting the run.
- Keep facts / interpretation / hypotheses / recommendations structurally separate; recommendations cite existing evidence only.
- Bridge messages ≤ 1 MiB; page/aggregate; use `artifact.open` for large exports.

## 4. Follow the build order

Work milestone-by-milestone per `BLUEPRINT.md` §5–6. **Do not start a milestone whose dependency isn't accepted.** Phase 0 (packaging/bridge/worker spike on both OS) precede any engine or UI code — if the spike fails, surface it and revise the ADR rather than coding around it.

## 5. Metadata-driven, not per-dataset

Behavior comes from the metric registry + dataset config + rules, never `profile_<domain>()` functions. New datasets are added by registering source + config, not rewriting algorithms. Invalid config/rules are rejected before profiling.

## 6. Testing is part of the task

- Every metric + rule type needs an independent reference test (`TESTING_BENCHMARKS.md` §3.1) — this is mandatory because we own the engine (ADR-012).
- Don't claim "done" until the relevant acceptance checklist passes; MVP acceptance is on the **packaged build on both OS**, not dev mode.
- Run/leave unit tests green; add contract + golden-snapshot tests for anything crossing module/process/language boundaries.

## 7. Security defaults

- Bridge = allow-listed typed facade only; validate all input; path allow-list; no `eval`/arbitrary FS from JS.
- Never write credentials or raw sensitive values into logs, errors, reports, or `error_log`. Mask PII-like columns in default HTML/CSV.
- Use OS-native credential store (Phase 2); keep user data separate from app dir; honor upgrade/uninstall data policy.

## 8. Escalate instead of deciding

If a spec is ambiguous, contradictory, or a task seems to require violating §2–§3, **stop and raise it** (propose a superseding ADR) rather than making a silent fundamental decision in code. Small local implementation choices (naming, helper structure) are yours.

## 9. Conventions

- Python 3.12, typed; `pytest` tests. React + TypeScript frontend. DuckDB for compute, Polars only selectively for transformations DuckDB handles poorly.
- No placeholders/TODOs left where a spec'd behavior exists. Match existing module structure in `ARCHITECTURE_AND_ADRS.md` §3.
- Keep the doc pack the authority; if you change a contract/decision, update the owning doc + add a new ADR in the same change.

## 10. Context & credit protocol (200K window, limited credits — binding)

The spec pack is long-term memory; `STATE.md` is working memory. Conversation context is disposable — never rely on it to carry decisions.

**Session start (fixed, minimal reads):**
1. Read `AGENTS.md` (this file) + `STATE.md`.
2. Open **only** the spec sections the current task points to (e.g. "CONTRACTS §4.4" → that section, not the whole pack). Never re-read all six documents "to be safe".
3. Resume the task marked `NEXT`; do not start new scope.

**Session rules:**
- **One task = one working session** (a task must be completable in <1 context-window; tasks in STATE.md are pre-sized for this). If a task turns out bigger, split it in STATE.md before coding.
- **Edit surgically** (targeted diffs, append sections); do not rewrite whole files; do not regenerate files a task doesn't touch.
- **Cap output volume:** run tests quietly (`-q`, filtered), never paste large data files, full schemas, or long logs into chat; summarize with file/line references.
- **Checkpoint before you run out:** if the session feels long, update `STATE.md` (status + "gotchas") and stop — do not finish "one more thing".
- **Commit per completed task** with the task ID (`M2-T3: rule compiler …`) — git history is a memory backup.

**Session end (mandatory):**
1. Update the task's checkbox + `Status:` line in `STATE.md`.
2. Write the new **NEXT** pointer (exact task + exact files to open).
3. Log any discovery a future session would need as a one-line **GOTCHA** (e.g. DuckDB API quirk), so it is never re-discovered.

**Why this works:** the pack already forbids re-deciding architecture (§8), so a fresh session with only `AGENTS.md` + `STATE.md` + one spec section has everything needed — full restart cost of a lost context is < ~5K tokens.
