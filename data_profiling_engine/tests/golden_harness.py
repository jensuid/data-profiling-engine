"""M4-T1 golden-snapshot harness (TESTING §2 "golden result files"; §3.3
provenance & reproducibility; ENGINE §10 JSON writer).

Definitions
-----------
*Golden fixture*
    A small, deterministic, checked-in source file + matching
    ``DatasetConfig`` document under ``tests/fixtures/golden/``. The CSV
    bytes and the config JSON are the inputs; the ``*.golden.json`` file
    is the canonical result document the engine must continue to emit
    for that input pair (TESTING §3.3: *byte-identical content_hash +
    config_hash + engine_version and equal metrics*).

*Volatile path*
    A JSON pointer inside the emitted document whose value is a property
    of the machine or the wall clock (absolute file path, mtime, DuckDB
    and Python versions, ``started_at`` / ``finished_at`` / ``duration_ms``).
    These are *legitimately* different across runs and must never appear
    in a golden; they are redacted to stable placeholder tokens before
    the diff. Nothing is redacted that could hide a real behavioural
    change — every metric, provenance tag, fingerprint, check status and
    error entry stays in the snapshot.

Regeneration
------------
Set ``DPE_UPDATE_GOLDEN=1`` in the environment to *rewrite* the golden
file from the current engine output (used intentionally when a contract
change lands — a metric semantic shift, a new promoted field, an ADR
revision). The default test path never writes; it only diffs, so a plain
``pytest`` run cannot silently accept drift.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from data_profiling_engine.orchestration.rollup import (
    ProfileBundle,
    profile_dataset,
)
from data_profiling_engine.reporting import write_profile_json
from data_profiling_engine.schemas.models import DatasetConfig

#: Root of the checked-in golden corpus (add one fixture triple per dataset
#: you want regression-pinned: ``<name>.csv`` + ``<name>.config.json`` +
#: ``<name>.golden.json``).
GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "golden"

#: Sentinel set by a maintainer to re-emit a golden file instead of diffing
#: against it. Never set in CI.
UPDATE_FLAG = "DPE_UPDATE_GOLDEN"

#: Placeholder token used for every redacted volatile scalar. Kept as an
#: unambiguous, non-path-shaped string so a golden diff stays readable and
#: a stray real value cannot accidentally match.
REDACTED = "<VOLATILE>"

#: ``run.timing.duration_ms`` is typed ``integer`` by the §2 schema, so a
#: string placeholder would break schema validation of the checked-in
#: golden. The neutral integer ``0`` is used instead — it is a legal value
#: (schema ``minimum: 0``) and cannot be mistaken for a real measurement.
REDACTED_INT = 0


# ---------------------------------------------------------------------------
# Volatile-path registry — the only fields a golden never sees
# ---------------------------------------------------------------------------
# ``run`` block: fields derived from the wall clock, the local filesystem
# layout, or the runtime library versions. Every other path (metrics,
# provenance.method, fingerprints, check statuses, findings, recommendations,
# content_hash, config_hash, engine_version) stays verbatim so the snapshot
# is a real regression net (TESTING §3.3).
_VOLATILE_RUN_FIELDS = (
    "source_ref",                # dataset.source_ref — absolute path
    "duckdb_version",            # engine.duckdb_version — env
    "python_version",            # engine.python_version — env
    "started_at",                # timing.started_at — wall clock
    "finished_at",               # timing.finished_at — wall clock
    "duration_ms",               # timing.duration_ms — wall clock
    "result_refs",               # artifact path map — set by CLI caller
)
_VOLATILE_SNAPSHOT_FIELDS = (
    "path",                      # source_snapshot.path — absolute path
    "modified_at",               # source_snapshot.modified_at — file mtime
)

#: ``QualityCheckResult.executed_at`` (CONTRACTS §4.1) is stamped by
#: :func:`quality.executor._now_iso` at rule-execution time. It is the only
#: wall-clock field that lives on a *nested* block, so it needs its own
#: redaction pass — every other timestamp sits on ``run`` and is covered by
#: the two lists above.
_VOLATILE_CHECK_FIELDS = ("executed_at",)


def normalize_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of a result document with volatile values redacted.

    The mutation is deliberately narrow — only the pointers named above
    are touched. Anything else that ends up differing between runs is
    either a genuine metric drift (which is what TESTING §2 asks the
    golden to catch) or a bug in this harness, and both must fail loudly.
    """
    out = json.loads(json.dumps(doc))  # deep copy via JSON round-trip
    run = out.get("run")
    if isinstance(run, dict):
        dataset = run.get("dataset")
        if isinstance(dataset, dict):
            for key in _VOLATILE_RUN_FIELDS:
                if key == "source_ref" and key in dataset:
                    dataset[key] = REDACTED
            snapshot = dataset.get("source_snapshot")
            if isinstance(snapshot, dict):
                for key in _VOLATILE_SNAPSHOT_FIELDS:
                    if key in snapshot:
                        snapshot[key] = REDACTED
        engine = run.get("engine")
        if isinstance(engine, dict):
            for key in ("duckdb_version", "python_version"):
                if key in engine:
                    engine[key] = REDACTED
        timing = run.get("timing")
        if isinstance(timing, dict):
            for key in ("started_at", "finished_at"):
                if key in timing:
                    timing[key] = REDACTED
            if "duration_ms" in timing:
                timing["duration_ms"] = REDACTED_INT
        if "result_refs" in run:
            run["result_refs"] = REDACTED
    checks = out.get("checks")
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict):
                for key in _VOLATILE_CHECK_FIELDS:
                    if key in check:
                        check[key] = REDACTED
    return out


def dump_stable(doc: Dict[str, Any]) -> str:
    """Deterministic pretty JSON for golden storage / diff (``sort_keys``,
    2-space indent, trailing newline). Matches ``reporting.write_profile_json``
    so the golden bytes and the produced file are the same shape."""
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Fixture loading + emission
# ---------------------------------------------------------------------------
def load_config(name: str) -> DatasetConfig:
    """Load ``<name>.config.json`` and validate it against the frozen
    ``dataset_config`` schema — a golden harness must reject an invalid
    config exactly the way the CLI does (AGENTS §5, TESTING §3.5)."""
    from data_profiling_engine import schemas as S

    path = GOLDEN_DIR / f"{name}.config.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    errors = S.validate("dataset_config.schema.json", document)
    assert errors == [], f"golden config {name!r} invalid: {errors}"
    return DatasetConfig.model_validate(document)


def emit_result_document(name: str, *, source: Optional[Path] = None,
                         run_id: str = "run-golden") -> Dict[str, Any]:
    """Run ``profile_dataset`` on the checked-in fixture and return the
    canonical result document (still carrying every volatile field — the
    caller decides what to redact).

    ``source`` defaults to ``<name>.csv`` in the golden dir; override for
    formats other than CSV. The write-to-disk path is exercised inside a
    ``tmp_path``-free scratch dir so the harness is test-runner-agnostic."""
    import tempfile

    cfg = load_config(name)
    csv_path = source if source is not None else GOLDEN_DIR / f"{name}.csv"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "profile.json"
        bundle: ProfileBundle = profile_dataset(
            cfg.dataset.name or name, csv_path, cfg, run_id,
            allowed_roots=[GOLDEN_DIR],
        )
        # write + read back so the golden exercises the on-file bytes of the
        # deterministic JSON writer (reporting.write_profile_json), not just
        # the in-memory dict.
        write_profile_json(bundle, out)
        return json.loads(out.read_text(encoding="utf-8"))


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.golden.json"


def read_or_update_golden(name: str, produced: Dict[str, Any]) -> str:
    """Return the golden text for ``name``, regenerating it in-place if
    ``DPE_UPDATE_GOLDEN=1`` is set. Always returns the reference string the
    caller should diff against."""
    gp = golden_path(name)
    if os.environ.get(UPDATE_FLAG) == "1":
        gp.write_text(dump_stable(produced), encoding="utf-8")
    if not gp.is_file():
        raise AssertionError(
            f"golden file missing for {name!r}: {gp}. "
            f"Regenerate with DPE_UPDATE_GOLDEN=1 pytest."
        )
    return gp.read_text(encoding="utf-8")


__all__ = [
    "GOLDEN_DIR",
    "REDACTED",
    "UPDATE_FLAG",
    "dump_stable",
    "emit_result_document",
    "golden_path",
    "load_config",
    "normalize_document",
    "read_or_update_golden",
]
