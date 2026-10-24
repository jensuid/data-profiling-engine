"""Finding fingerprint algorithm — CONTRACTS §4.4 / ADR-016.

A ``fingerprint`` gives a finding **cross-run identity**: two profiling runs
that surface the *same* problem but compute *different* numbers must produce the
same digest, so later drift/baseline comparison (ENGINE §9) and dedup can match
them. Identity therefore rides the *stable location + measure*, never the value.

    fingerprint = sha256( category | scope_key | detector_id | normalized_measure )

- ``scope_key`` = ``dataset_id + (table?) + column`` — the stable location.
- ``normalized_measure`` = the metric/rule *identity* (e.g. ``missing_rate``),
  never its value.
- ``detector_id`` distinguishes the detecting mechanism so two different probes
  of the same column/measure are different findings.

Fields are joined with the ASCII ``Unit Separator`` (0x1F) and scope sub-parts
with ``Record Separator`` (0x1E): control characters that cannot appear in a
dataset/column identifier, so no two distinct field tuples can collide into the
same concatenated string (a plain ``|`` join would be ambiguous once a scope_key
already embeds separators). Output is the lowercase hex digest prefixed
``sha256:`` to match the frozen ``finding.schema.json`` pattern
``^sha256:[0-9a-f]{64}$``.
"""
from __future__ import annotations

import hashlib
from typing import Optional

#: Join characters (see module docstring) — chosen to be collision-safe.
_FIELD_SEP = "\x1f"   # between the four §4.4 top-level fields
_SCOPE_SEP = "\x1e"   # inside a scope_key (dataset_id / table / column)


def build_scope_key(
    dataset_id: str, column: Optional[str], table: Optional[str] = None
) -> str:
    """Stable location of the issue: ``dataset_id + (table?) + column`` (§4.4).

    A ``None`` column (a dataset-scoped rule) is encoded as the empty string so
    the digest is still deterministic; ``table`` is omitted for MVP file sources
    (CSV/Parquet, ADR-011) and reserved for Phase-2 warehouse tables.
    """
    parts = [dataset_id, table or ""]
    parts.append(column if column is not None else "")
    return _SCOPE_SEP.join(parts)


def compute_fingerprint(
    category: str,
    scope_key: str,
    detector_id: str,
    normalized_measure: str,
) -> str:
    """Deterministic ``sha256:`` fingerprint over the four §4.4 identity fields.

    All inputs are the *identity* strings, never a measured value, so the digest
    is stable while the underlying number changes across runs (ADR-016).
    """
    payload = _FIELD_SEP.join([category, scope_key, detector_id, normalized_measure])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


__all__ = ["build_scope_key", "compute_fingerprint"]
