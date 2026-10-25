---
title: "Security & Packaging — Data Profiling Engine"
aliases: [Security, Packaging]
tags: [security, packaging, installer, pywebview, pyinstaller]
status: active
created: 2026-10-24
doc_role: supporting
---

# Security Contract & Packaging / Deployment

> [!NOTE] Boundaries
> Bridge message *shapes* are in [[CONTRACTS]] §5; this document defines the **security constraints on the bridge**, credential handling, data protection, directory layout, and the macOS/Windows packaging pipeline. All packaging here is gated by ADR-009 (PyInstaller pending Phase-0 validation).

## 1. Security posture (principles)

- **Read-only by default** against every data source; the engine never mutates or deletes source data (also a product principle).
- **Least-privilege bridge**: the JS↔Python surface exposes only allow-listed commands.
- **No network listeners**: per ADR-005, the app binds no HTTP/TCP port; only outbound connections to explicitly configured external data sources (Phase 2) are permitted.
- **Data minimization**: don't persist raw data the product doesn't need; mask sensitive values in reports; keep credentials out of logs/results.

## 2. Directory separation (binding)

Directories are resolved via OS-standard locations, **never hard-coded**. Install directory must be separate from user data so upgrades never destroy results.

| Directory | Contents | Upgrade behavior |
|---|---|---|
| Application | executable + bundled libs | replaced on upgrade |
| User data | config, SQLite metadata, run history, findings | preserved |
| Cache | re-creatable derived data | may be cleared |
| Temporary workspace | staging, in-progress artifacts | cleaned by policy |
| Export | user-initiated report exports | never auto-deleted |

macOS: `~/Library/Application Support/<App>` (data), `~/Library/Caches/<App>` (cache).
Windows: `%APPDATA%\<App>` (data), `%LOCALAPPDATA%\<App>\Cache` (cache), `%TEMP%` (workspace).

DuckDB spill blocks (ADR-021) live in the **temporary workspace** — `<temp>/<App>/duckdb_scratch`, created owner-only (`0700`; per-user ACL on Windows) and capped in size, never in the application bundle and never in the artifact store. They are unmasked fragments of the source file, so the workspace is treated as user-private and is safe to clear: nothing unique is kept there. The engine refuses a relative `temp_directory` (DuckDB's own default for an in-memory database is `.tmp`, which would resolve against the process CWD) — see [[ARCHITECTURE_AND_ADRS]] ADR-021.

Lifecycle rules: user data survives upgrade; cache is rebuildable; temp files have a cleanup policy; exports are removed only by explicit user action; **metadata schema changes go through migrations** (§6 here + [[CONTRACTS]] persistence).

## 3. Bridge security (enforces [[CONTRACTS]] §5)

- **Typed command facade.** Only the allow-listed commands are exposed; unknown command → `BRIDGE_COMMAND_UNKNOWN`. No `eval`, no arbitrary Python call, no arbitrary filesystem access from JS.
- **Full input validation.** Every payload validated by schema (pydantic) before dispatch; reject unknown fields.
- **Path allow-list.** `dataset.*`/`artifact.open` operate only on paths the user explicitly selected or that are inside allowed roots; out-of-scope path → `SOURCE_NOT_ALLOWED`.
- **Encoding.** Never interpolate untrusted data into JS without encoding; results are structured JSON, not executable strings.
- **No untrusted external content with bridge privileges.** The WebView must not load remote/untrusted HTML with the bridge injected; only local app assets (ADR-010).
- **Payload cap.** 1 MiB per message ([[CONTRACTS]] §5.4); over → `BRIDGE_PAYLOAD_TOO_LARGE`, fall back to paging/`artifact.open`.

## 4. Data security & sensitive-value handling

- **File access scope:** only user-chosen/allowed locations.
- **Masking / redaction in reports:** Pattern/semantic inference flags probable PII columns (email, phone, national-id-like patterns). For these, `sample_row_refs` render **masked values** (e.g. `j***@…`, partial digits) in HTML/CSV; full values only in access-controlled JSON artifacts and only when config explicitly permits. Default: mask.
- **No raw data hoarding:** avoid persisting raw copies not required; staging files cleaned after run.
- **User control:** clear UI/settings for cache, staging, and local history (including "delete this dataset's history and artifacts").

## 5. Credential security (Phase 2 sources, defined now)

MVP file-only sources need no credentials, but the contract is set now so Phase-2 DB/warehouse fits without redesign:

- Use the **OS-native credential store** (macOS Keychain; Windows Credential Manager) — never plaintext in config or SQLite.
- Credentials **never** appear in logs, error messages, reports, or `run.error_log`.
- Support connection removal + stored-credential deletion.
- DB/warehouse connections **read-only by default**.
- Config files may reference a credential *alias*, not the secret.

## 6. Packaging & deployment

### 6.1 Strategy

- **PyInstaller** bundles the Python runtime + engine deps into a per-OS executable (ADR-009, validate in Phase 0).
- **Frontend**: React/TS built to static assets, HashRouter (ADR-013), loaded from local files (ADR-010) — no dev server in production.
- **Separate native builds** per OS on native environments (no cross-compilation assumption).

### 6.2 Known packaging risks to validate in Phase 0

| Risk | Mitigation / test |
|---|---|
| DuckDB native lib not collected (`.dylib`/`.dll`) | explicit hidden-import/binaries in spec; smoke-test a query in packaged build |
| pywebview JS assets missing | verify bundled webview runtime files |
| `multiprocessing` spawn in frozen app | worker entry re-registration (`set_start_method('spawn')`, `freeze_support()`), proven on Windows |
| Antivirus false positives (Win) | code signing; test on clean machine |
| Slow startup / large installer | measure; consider on-demand imports |
| WebView2 absent (Win) | bootstrapper detection/install or graceful error |
| macOS Gatekeeper | codesign + notarization |

### 6.3 macOS specifics

- `.app` bundle → `.dmg`; codesign + notarize for distribution.
- WKWebView is system-provided (no extra runtime), but behavior must be tested on the minimum supported macOS.

### 6.4 Windows specifics

- Ensure **WebView2 Runtime** availability (bootstrapper in installer or documented prerequisite).
- Installer (e.g. NSIS/MSI) places app in Program Files, data in `%APPDATA%`; must handle per-user vs all-users cleanly.
- Test antivirus interaction and code signing.

### 6.5 Upgrade, rollback, uninstall

- Upgrade replaces application directory only; user data/cache/export preserved (§2).
- Metadata migrations run forward on first launch of new version; document schema_version (from [[CONTRACTS]] §1) and keep a pre-migration backup of SQLite.
- Rollback: schema changes must be backward-compatible where feasible (additive, per [[CONTRACTS]] versioning); otherwise restore-from-backup path documented.
- Uninstall does **not** silently delete user data; explicit opt-in required to remove results.

### 6.6 Build pipeline (two-track)

```text
shared: build React assets → validate schemas/contracts → run unit+integration tests
macOS track: PyInstaller (mac) → .app → codesign+notarize → .dmg → smoke tests on macOS
Windows track: PyInstaller (win) → exe → install WebView2 prereq → sign → installer → smoke tests on Windows
release gate: acceptance tests in TESTING_BENCHMARKS on BOTH OS from the packaged installer, not dev mode
```

> [!IMPORTANT]
> A successful run in the development environment proves nothing about the installer. The release gate is the **packaged app** passing smoke + core job tests on each OS.

## 7. Logging & diagnostics security

- Structured logs with levels; error objects follow the [[CONTRACTS]] §8 registry.
- Scrub pipeline guarantees no credentials or masked-category raw values reach logs or diagnostic artifacts.
- Crash/worker diagnostics written to the temporary/diagnostic area with the same masking guarantees; safe to share.

## 8. Security acceptance (cross-ref [[TESTING_BENCHMARKS]] §5.4)

- No port bound while app runs (automated check).
- Bridge rejects unknown commands, bad schemas, out-of-scope paths, oversized payloads.
- PII-like columns masked in default HTML/CSV export.
- Credentials absent from every log/result sample; stored only in native store (Phase 2).
- Upgrade preserves user data; uninstall preserves user data unless opted-in.
