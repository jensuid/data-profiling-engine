"""Findings engine — detectors → candidates → dedup/merge → prioritize →
contract :class:`Finding` objects (M2-T8, ENGINE §6; CONTRACTS §4.2 / §4.4).

The *interpreting* layer of the measure ≠ judge ≠ interpret ≠ act pipeline: it
consumes **only existing evidence** — the M2-T7 :class:`QualityCheckResult`
list (``FAIL``/``WARN`` ⇒ findings) and the M2-T4..T6 :class:`ColumnProfile`
extremes that have *no rule behind them* (constant column, near-total
missingness, dominant category, duplicated key candidates) — and never invents a
number (AGENTS §3). Every fact keeps the ``Provenance`` it was measured with,
and every finding carries a deterministic ADR-016 ``fingerprint`` so cross-run
identity is available to the Phase-4 drift comparator.

Public surface::

    from data_profiling_engine.findings import build_findings

    result = build_findings(
        dataset_id, run_id, config,
        profiles=profile_columns, checks=quality.checks,
        duplicate_group_counts=profiler.duplicate_group_counts,
        row_count=row_count,
    )
    for finding in result.findings:      # prioritized, deduped, §4.2-valid
        ...

Submodules: :mod:`findings.fingerprinting` (§4.4 / ADR-016),
:mod:`findings.detectors` (§6.1), :mod:`findings.pipeline` (§6.2–6.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from data_profiling_engine.findings.detectors import (
    DOMINANT_CATEGORY_SHARE,
    NEAR_TOTAL_MISSINGNESS,
    Candidate,
    detect_from_profiles,
    detect_from_quality_checks,
)
from data_profiling_engine.findings.fingerprinting import (
    build_scope_key,
    compute_fingerprint,
)
from data_profiling_engine.findings.pipeline import run_pipeline
from data_profiling_engine.schemas.models import (
    ColumnProfile,
    DatasetConfig,
    Finding,
    QualityCheckResult,
)


@dataclass(frozen=True)
class FindingsResult:
    """Outcome of one findings pass: prioritized, deduped, contract-valid
    findings ordered most-urgent-first (``F-001`` is the highest priority)."""

    findings: Tuple[Finding, ...]


def build_findings(
    dataset_id: str,
    run_id: str,
    config: DatasetConfig,
    profiles: Sequence[ColumnProfile],
    checks: Sequence[QualityCheckResult],
    duplicate_group_counts: Optional[Dict[str, int]] = None,
    row_count: Optional[int] = None,
) -> FindingsResult:
    """Run the full §6 pipeline over existing evidence.

    ``profiles`` are the assembled :class:`ColumnProfile`s (discovery skeleton +
    M2-T4..T6 metric blocks); ``checks`` the M2-T7 quality results;
    ``duplicate_group_counts`` the M2-T5 uniqueness auxiliary (the metric whose
    ``contract_target`` is ``None``); ``row_count`` the dataset population used
    only to weight affected-record *share* in prioritization (never emitted —
    the contract has no such field). Critical scope is taken from
    ``config.dataset.primary_key`` (§6.2).
    """
    candidates = list(detect_from_quality_checks(dataset_id, tuple(checks)))
    candidates += detect_from_profiles(dataset_id, tuple(profiles), duplicate_group_counts)
    critical = list(config.dataset.primary_key or [])
    findings = run_pipeline(candidates, run_id, row_count, critical)
    return FindingsResult(findings=findings)


__all__ = [
    "build_findings",
    "FindingsResult",
    "Candidate",
    "detect_from_quality_checks",
    "detect_from_profiles",
    "compute_fingerprint",
    "build_scope_key",
    "NEAR_TOTAL_MISSINGNESS",
    "DOMINANT_CATEGORY_SHARE",
]
