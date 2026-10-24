"""M4-T1 golden-snapshot tests — the formal regression net over the
deterministic JSON writer (ENGINE §10; TESTING §2 "golden result files";
TESTING §3.3 reproducibility).

Complements (does not duplicate) ``test_cli_rollup.py``:

* ``test_cli_rollup``  — proves the rollup + writer *behave* correctly on a
  freshly generated in-memory fixture (schema shape, FAIL/ERROR policy,
  mode / sampling coverage, CLI wiring).
* this module          — pins the writer's *exact output* for a checked-in
  fixture against a canonical golden file so any accidental metric drift,
  provenance mislabeling, or fingerprint change is caught with a plain
  ``git diff`` on the golden (regression snapshot, TESTING §2).

Adding a fixture is one triple of files under ``fixtures/golden/`` —
``<name>.csv`` + ``<name>.config.json`` + ``<name>.golden.json``. Bump an
existing golden intentionally with ``DPE_UPDATE_GOLDEN=1 pytest`` and
review the resulting diff before committing.
"""
from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.reporting import RESULT_DOC_KEYS
from data_profiling_engine.tests.golden_harness import (
    GOLDEN_DIR,
    REDACTED,
    REDACTED_INT,
    dump_stable,
    emit_result_document,
    golden_path,
    load_config,
    normalize_document,
    read_or_update_golden,
)

# Fixtures covered by the golden corpus. Adding a name here without also
# adding the file triple will fail loudly (``read_or_update_golden``).
GOLDEN_FIXTURES = ("sales_deals",)

# ---------------------------------------------------------------------------
# 1. Byte-level golden equality (TESTING §2 "canonical JSON outputs")
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fixture_name", GOLDEN_FIXTURES)
def test_emitted_document_matches_golden(fixture_name: str) -> None:
    """Normalize the emitted result document and diff its canonical text
    against the checked-in golden. Any metric drift, method mislabel,
    fingerprint change, or new field surfaces here as a readable
    line-oriented diff (pytest shows the unified-diff of the two strings)."""
    produced = normalize_document(emit_result_document(fixture_name))
    reference = read_or_update_golden(fixture_name, produced)
    candidate = dump_stable(produced)
    if candidate != reference:
        # Provide a compact, actionable failure — full text is huge; show
        # the first mismatching line index so a maintainer can jump to it.
        ref_lines = reference.splitlines()
        cand_lines = candidate.splitlines()
        first_diff = next(
            (i for i, (a, b) in enumerate(zip(ref_lines, cand_lines)) if a != b),
            min(len(ref_lines), len(cand_lines)),
        )
        ctx = slice(max(0, first_diff - 3), first_diff + 4)
        raise AssertionError(
            f"golden drift for {fixture_name!r} at line {first_diff + 1}\n"
            f"--- golden ---\n" + "\n".join(ref_lines[ctx]) + "\n"
            f"+++ emitted +++\n" + "\n".join(cand_lines[ctx]) + "\n"
            f"(regenerate intentionally with DPE_UPDATE_GOLDEN=1)"
        )


# ---------------------------------------------------------------------------
# 2. Every golden block is still schema-valid (TESTING §3.5 + AGENTS §3)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fixture_name", GOLDEN_FIXTURES)
def test_golden_blocks_schema_valid(fixture_name: str) -> None:
    """The golden is a *contract* artifact — every block it holds must
    validate against its JSON Schema, not just the run's in-memory model.
    A test on the emitted document would be satisfied by pydantic defaults
    alone; validating the checked-in file directly proves the golden stays
    well-formed even after a maintainer regenerates it."""
    text = golden_path(fixture_name).read_text(encoding="utf-8")
    doc = json.loads(text)
    assert set(doc) == set(RESULT_DOC_KEYS)
    assert S.validate("profiling_run.schema.json", doc["run"]) == []
    assert S.validate("dataset_profile.schema.json", doc["profile"]) == []
    for col in doc["profile"]["columns"]:
        assert S.validate("column_profile.schema.json", col) == []
    for check in doc["checks"]:
        assert S.validate("quality_check_result.schema.json", check) == []
    for finding in doc["findings"]:
        assert S.validate("finding.schema.json", finding) == []
    for rec in doc["recommendations"]:
        assert S.validate("recommendation.schema.json", rec) == []


# ---------------------------------------------------------------------------
# 3. Volatile redaction actually works — the golden must NOT be a machine
#    fingerprint and must NOT hide the reproducibility tuple.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fixture_name", GOLDEN_FIXTURES)
def test_volatile_paths_are_redacted(fixture_name: str) -> None:
    """Every field the harness claims is volatile must appear in the golden
    as the placeholder token — otherwise a future change (e.g. rollup
    stops stamping ``duration_ms``) could silently drop a real value into
    the machine-dependent bucket and this test would catch it. Conversely,
    none of the reproducibility tuple may be redacted: it is the whole
    reason the golden is trustworthy."""
    text = golden_path(fixture_name).read_text(encoding="utf-8")
    doc: Dict[str, Any] = json.loads(text)
    run = doc["run"]

    # volatile → REDACTED
    assert run["dataset"]["source_ref"] == REDACTED
    assert run["dataset"]["source_snapshot"]["path"] == REDACTED
    assert run["dataset"]["source_snapshot"]["modified_at"] == REDACTED
    assert run["engine"]["duckdb_version"] == REDACTED
    assert run["engine"]["python_version"] == REDACTED
    assert run["timing"]["started_at"] == REDACTED
    assert run["timing"]["finished_at"] == REDACTED
    assert run["timing"]["duration_ms"] == REDACTED_INT
    # nested: every QualityCheckResult.executed_at is a wall-clock stamp
    for check in doc["checks"]:
        assert check["executed_at"] == REDACTED, check.get("rule_id")

    # reproducibility tuple → NOT redacted (TESTING §3.3)
    assert run["dataset"]["source_snapshot"]["content_hash"].startswith("sha256:")
    assert run["config"]["config_hash"].startswith("sha256:")
    assert run["engine"]["engine_version"] != REDACTED
    assert run["engine"]["engine_version"]  # non-empty


# ---------------------------------------------------------------------------
# 4. Reproducibility under repeat emission (TESTING §3.3)
# ---------------------------------------------------------------------------
def test_repeat_emission_is_byte_stable_after_normalization() -> None:
    """Emitting twice against the same fixture + config + engine version
    must produce byte-identical *normalized* documents. The volatile
    fields legitimately differ (mtime is the same file but wall-clock
    moves); everything else — content_hash, config_hash, engine_version,
    every metric, every fingerprint — is required to be equal."""
    a = normalize_document(emit_result_document("sales_deals"))
    b = normalize_document(emit_result_document("sales_deals"))
    assert dump_stable(a) == dump_stable(b)


# ---------------------------------------------------------------------------
# 5. Known-issue regression (TESTING §2 "end-to-end scenario with known
#    expected findings"). Cheap cross-check that the golden still reflects
#    the injected defects — a future refactor that silently stops emitting
#    these FAILs must fail here *and* in the golden diff.
# ---------------------------------------------------------------------------
def test_golden_carries_the_known_injected_issues() -> None:
    doc = json.loads(golden_path("sales_deals").read_text(encoding="utf-8"))
    fails = {c["rule_id"] for c in doc["checks"] if c["status"] == "FAIL"}
    # duplicates on deal_id + missing deal_value → both must FAIL;
    # stage_domain passes (all values in the enumerated domain)
    assert "deal_id_unique" in fails
    assert "value_not_null" in fails
    passes = {c["rule_id"] for c in doc["checks"] if c["status"] == "PASS"}
    assert "stage_domain" in passes
    # each FAIL must be surfaced as a Finding, and every Finding must cite
    # at least one Recommendation (CONTRACTS §4.2 / §4.3 wiring)
    assert doc["findings"], "golden has no findings — the fixture is broken"
    for finding in doc["findings"]:
        assert finding["fingerprint"].startswith("sha256:")
        assert finding["recommendation_ids"], finding["finding_id"]
    # row_count stays exact under the standard mode (no sampling enabled)
    assert doc["profile"]["row_count"]["provenance"]["method"] == "exact"
    assert doc["profile"]["row_count"]["value"] == 40


# ---------------------------------------------------------------------------
# 6. Config-vs-golden coupling: config_hash in the golden must match a fresh
#    ``config_hash(load_config(name))`` — proves the checked-in config file
#    is what produced the checked-in golden (no silent drift between the two).
# ---------------------------------------------------------------------------
def test_golden_config_hash_matches_checked_in_config() -> None:
    from data_profiling_engine.orchestration.rollup import config_hash

    cfg = load_config("sales_deals")
    doc = json.loads(golden_path("sales_deals").read_text(encoding="utf-8"))
    assert doc["run"]["config"]["config_hash"] == config_hash(cfg)


# ---------------------------------------------------------------------------
# 7. The golden dir must exist and the fixture triple must be complete —
#    a broken checkout (missing CSV / missing config) fails with a clear
#    message instead of a mysterious pydantic / DuckDB error.
# ---------------------------------------------------------------------------
def test_fixture_triple_present() -> None:
    assert GOLDEN_DIR.is_dir() and GOLDEN_DIR.name == "golden"
    for name in GOLDEN_FIXTURES:
        for suffix in (".csv", ".config.json", ".golden.json"):
            path = GOLDEN_DIR / f"{name}{suffix}"
            assert path.is_file(), f"missing fixture component: {path}"


# ---------------------------------------------------------------------------
# 8. Harness self-check: ensure `normalize_document` is a real deep copy so
#    mutating the returned doc never touches the caller's input. (Cheap to
#    prove; expensive to debug if a future refactor breaks it.)
# ---------------------------------------------------------------------------
def test_normalize_document_does_not_mutate_input() -> None:
    original: Dict[str, Any] = {
        "run": {
            "dataset": {"source_ref": "/tmp/x.csv", "source_snapshot": {"path": "/tmp/x.csv"}},
            "engine": {"duckdb_version": "1.0"},
            "timing": {"started_at": "2026-01-01T00:00:00Z", "duration_ms": 7},
        }
    }
    snapshot = json.loads(json.dumps(original))
    out = normalize_document(original)
    assert original == snapshot, "normalize_document mutated its input"
    assert out["run"]["dataset"]["source_ref"] == REDACTED
    assert out["run"]["timing"]["duration_ms"] == REDACTED_INT
