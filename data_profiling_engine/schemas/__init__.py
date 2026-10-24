"""Canonical JSON Schemas for cross-boundary contracts (CONTRACTS §1; ADR-014).

M1-T1 scope: Provenance, ProfilingRun, DatasetProfile, ColumnProfile.
M1-T2 adds QualityCheckResult / Finding / Recommendation / FindingWorkflow;
M1-T3 adds the dataset-config schema; M1-T6 adds bridge/job envelopes.

Every schema file in this directory follows ``<name>.schema.json`` and carries
a stable ``$id`` under ``https://dpe.local/schemas/``. Cross-file ``$ref``
resolve against the in-memory :data:`REGISTRY` built here — no network access
is performed at validation time.

Usage::

    from data_profiling_engine.schemas import validator_for

    v = validator_for("provenance.schema.json")
    errors = list(v.iter_errors(instance))

Format checking (RFC 3339 ``date-time`` etc.) is enabled via
``Draft202012Validator.FORMAT_CHECKER``. Callers that need stricter checks can
pass their own ``format_checker``.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_DIR: Path = Path(__file__).resolve().parent

# Files that must be present for the M1-T1 slice; enforced by tests so a
# missing/renamed schema fails loudly rather than silently falling back.
REQUIRED_SCHEMAS: tuple[str, ...] = (
    "provenance.schema.json",
    "profiling_run.schema.json",
    "dataset_profile.schema.json",
    "column_profile.schema.json",
)


def _load_raw() -> Dict[str, dict]:
    """Read every ``*.schema.json`` file in ``SCHEMA_DIR`` keyed by filename."""
    out: Dict[str, dict] = {}
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        with path.open("r", encoding="utf-8") as fh:
            out[path.name] = json.load(fh)
    return out


RAW_SCHEMAS: Dict[str, dict] = _load_raw()


def _by_id() -> Dict[str, dict]:
    """Map each schema's ``$id`` URI to its parsed body."""
    result: Dict[str, dict] = {}
    for name, body in RAW_SCHEMAS.items():
        sid = body.get("$id")
        if not sid:
            raise RuntimeError(
                f"schema file {name!r} is missing a top-level $id "
                "(required for cross-file $ref resolution)"
            )
        result[sid] = body
    return result


BY_ID: Dict[str, dict] = _by_id()

# ``referencing.Registry`` populated with all local schemas so that any
# ``$ref: https://dpe.local/schemas/xxx.schema.json#/$defs/yyy`` resolves
# without touching the network.
REGISTRY: Registry = Registry().with_resources(
    [(uri, Resource(contents=body, specification=DRAFT202012)) for uri, body in BY_ID.items()]
)


@lru_cache(maxsize=None)
def _validator_for(filename: str, check_format: bool) -> Draft202012Validator:
    try:
        body = RAW_SCHEMAS[filename]
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError(
            f"unknown schema {filename!r}; available: {sorted(RAW_SCHEMAS)}"
        ) from exc
    # Draft202012Validator anchors on the schema's own $id so relative refs
    # and root-pointer refs ("#") resolve against the correct resource.
    return Draft202012Validator(
        body,
        registry=REGISTRY,
        format_checker=FormatChecker() if check_format else None,
    )


def validator_for(filename: str, *, check_format: bool = True) -> Draft202012Validator:
    """Return a cached Draft 2020-12 validator bound to the local schema registry.

    ``filename`` is the on-disk name (e.g. ``"provenance.schema.json"``); pass
    ``check_format=False`` in tests that intentionally use non-RFC3339 sample
    timestamps but still want structural validation.
    """
    return _validator_for(filename, check_format)


def validate(filename: str, instance: object, *, check_format: bool = True) -> list[str]:
    """Convenience: return a list of human-readable validation errors (empty = ok)."""
    v = validator_for(filename, check_format=check_format)
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in v.iter_errors(instance)
    ]


__all__ = [
    "SCHEMA_DIR",
    "REQUIRED_SCHEMAS",
    "RAW_SCHEMAS",
    "BY_ID",
    "REGISTRY",
    "validator_for",
    "validate",
]
