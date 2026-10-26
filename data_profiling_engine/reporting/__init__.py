"""Reporting layer (M2-T11 JSON writer; ENGINE §10).

Built **only** from the structured run result — never re-runs statistics
(§10 first bullet). This module owns the canonical JSON serialisation of a
:class:`~data_profiling_engine.orchestration.rollup.ProfileBundle`: the
``ProfilingRun`` (§2), the ``DatasetProfile`` (§3.1) and every quality /
finding / recommendation object, emitted as one contract-shaped document for
programmatic use. The self-contained HTML report landed in M4-T2
(:mod:`reporting.html_report`) and the CSV issue export in M4-T3
(:mod:`reporting.csv_export`).

Determinism is a contract obligation here, not a nicety: the same input +
config + engine version must produce a byte-identical file (TESTING §3.3), so
the dump is ``sort_keys=True`` and every nested object serialises through its
pydantic ``model_dump(mode="json")`` (which already omits absent keys per the
strict-contract policy in ``schemas/models.py``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from data_profiling_engine.orchestration.rollup import ProfileBundle

#: Stable top-level keys of the canonical result document (ENGINE §10 "JSON:
#: the canonical result (all contracts)"). The names mirror the contract
#: objects so a consumer can map each block to its JSON Schema directly.
RESULT_DOC_KEYS = ("run", "profile", "checks", "findings", "recommendations")


def _dump(obj: Any) -> Any:
    """One contract object → JSON-safe primitive via its own model dump."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def result_document(bundle: ProfileBundle) -> Dict[str, Any]:
    """Build the canonical in-memory JSON document for one run.

    ``profile.columns`` carries the full §3.2 ColumnProfile array; ``checks`` /
    ``findings`` / ``recommendations`` are the ordered §4.1 / §4.2 / §4.3
    result lists. Nothing is recomputed — every number already rides with the
    ``Provenance`` the engine stamped (ADR-015)."""
    return {
        "run": _dump(bundle.run),
        "profile": _dump(bundle.profile),
        "checks": [_dump(c) for c in bundle.checks],
        "findings": [_dump(f) for f in bundle.findings],
        "recommendations": [_dump(r) for r in bundle.recommendations],
    }


def write_profile_json(bundle: ProfileBundle, out_path: "Path | str") -> Path:
    """Serialise :func:`result_document` to ``out_path`` as deterministic JSON
    and return the written path (the ``result_refs.profile_json`` target).

    Parent directories are created so a CLI ``--out artifacts/x/profile.json``
    just works; the file is UTF-8 with a trailing newline and no per-key
    re-ordering surprises (``sort_keys=True`` makes golden-file diffs stable)."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result_document(bundle), indent=2, sort_keys=True, ensure_ascii=False)
    # newline="\n": byte-stable across OSes; default text mode = CRLF on Windows
    out.write_text(text + "\n", encoding="utf-8", newline="\n")
    return out


__all__ = [
    "RESULT_DOC_KEYS",
    "result_document",
    "write_profile_json",
]
