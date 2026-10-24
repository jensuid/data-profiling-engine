"""Findings pipeline — dedup/merge, prioritization, contract assembly (M2-T8,
ENGINE §6 steps 3–5).

Candidates from :mod:`findings.detectors` are:

* **de-duplicated / merged** (§6.3) — same root ``(column, category)`` collapses
  to one finding to avoid alert spam; the highest-priority representative keeps
  its identity, and the absorbed candidates' facts / evidence refs / hypotheses
  are folded in (unioned, order-preserving, deduped).
* **prioritized** (§6.2) — ``priority = f(severity, affected_record_share,
  criticality_of_scope, confidence)``. ``criticality`` derives from the declared
  dataset primary key (CONTRACTS §9 ``dataset.primary_key``); confidence from the
  measuring method (exact > approximate > sampled). Priority is an *ordering*
  signal only — the contract ``Finding`` (§4.2) carries ``severity`` +
  ``confidence`` but no ``priority`` field, so nothing is added to the schema.
* **assembled** into frozen :class:`Finding` objects, ``finding_id`` assigned
  after a deterministic ``(-priority, fingerprint)`` sort so the run's most
  urgent item is always ``F-001``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from data_profiling_engine.discovery.inference import SCHEMA_VERSION
from data_profiling_engine.findings.detectors import Candidate
from data_profiling_engine.schemas.models import (
    Finding,
    FindingConfidence,
    FindingFact,
    FindingHypothesis,
    FindingScope,
    QualityDimension,
    Severity,
)

#: Severity ⇒ ordinal weight (§6.2 "by impact, not solely threshold breach").
_SEVERITY_RANK: Dict[Severity, int] = {
    Severity.CRITICAL: 5,
    Severity.HIGH: 4,
    Severity.MEDIUM: 3,
    Severity.LOW: 2,
    Severity.WARNING: 1,
    Severity.INFO: 0,
}
#: Confidence ⇒ tie-break weight (exact > approximate > sampled, §6.2).
_CONFIDENCE_RANK: Dict[FindingConfidence, int] = {
    FindingConfidence.HIGH: 3,
    FindingConfidence.MEDIUM: 2,
    FindingConfidence.LOW: 1,
}
#: Boost for a finding whose scope is a declared-critical column (PK/business).
_CRITICAL_BOOST = 1000
#: Weight on the affected-record share (0..1) scaled to an integer band.
_SHARE_SCALE = 500


@dataclass(frozen=True)
class PrioritizedCandidate:
    """A candidate paired with its computed priority (kept out of the contract)."""

    candidate: Candidate
    priority: int


def _share(affected_records: Optional[int], row_count: Optional[int]) -> float:
    if not affected_records or not row_count or row_count <= 0:
        return 0.0
    return min(1.0, affected_records / row_count)


def priority_of(
    candidate: Candidate,
    row_count: Optional[int],
    critical_columns: Sequence[str],
) -> int:
    """§6.2 composite priority — a deterministic integer, higher = more urgent.

    severity dominates, then scope criticality, then affected share, then
    confidence as the finest tie-break. ``critical_columns`` is the declared PK
    (business-critical columns already carry their own rule ``severity``).
    """
    sev = _SEVERITY_RANK.get(candidate.severity, 0)
    conf = _CONFIDENCE_RANK.get(candidate.confidence, 0)
    critical = 1 if (candidate.column and candidate.column in critical_columns) else 0
    share = _share(candidate.affected_records, row_count)
    return (
        sev * 100_000
        + critical * _CRITICAL_BOOST * 100
        + int(share * _SHARE_SCALE) * 10
        + conf
    )


def _merge_facts(existing: List[FindingFact], extra: Tuple[FindingFact, ...]) -> List[FindingFact]:
    seen = {f.metric_ref for f in existing}
    for fact in extra:
        if fact.metric_ref not in seen:
            existing.append(fact)
            seen.add(fact.metric_ref)
    return existing


def _merge_hypotheses(
    existing: List[FindingHypothesis], extra: Tuple[FindingHypothesis, ...]
) -> List[FindingHypothesis]:
    seen = {h.text for h in existing}
    for hyp in extra:
        if hyp.text not in seen:
            existing.append(hyp)
            seen.add(hyp.text)
    return existing


def dedup_and_merge(
    prioritized: Sequence[PrioritizedCandidate],
) -> List[PrioritizedCandidate]:
    """Collapse candidates sharing ``(column, category)`` into one representative.

    The highest-priority candidate survives with its own fingerprint/title/summary;
    the rest are absorbed — their facts / evidence refs / hypotheses are unioned
    into the survivor (order = descending priority). Insertion order of groups is
    preserved for a stable pre-sort.
    """
    groups: Dict[Tuple[Optional[str], QualityDimension], List[PrioritizedCandidate]] = {}
    order: List[Tuple[Optional[str], QualityDimension]] = []
    for item in prioritized:
        key = (item.candidate.column, item.candidate.category)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    merged: List[PrioritizedCandidate] = []
    for key in order:
        members = sorted(groups[key], key=lambda it: (-it.priority, it.candidate.fingerprint))
        winner = members[0]
        if len(members) == 1:
            merged.append(winner)
            continue
        facts = list(winner.candidate.facts)
        hypotheses = list(winner.candidate.hypotheses)
        evidence = list(winner.candidate.evidence_refs)
        for other in members[1:]:
            c = other.candidate
            facts = _merge_facts(facts, c.facts)
            hypotheses = _merge_hypotheses(hypotheses, c.hypotheses)
            for ref in c.evidence_refs:
                if ref not in evidence:
                    evidence.append(ref)
        combined = Candidate(
            detector_id=winner.candidate.detector_id,
            normalized_measure=winner.candidate.normalized_measure,
            category=winner.candidate.category,
            column=winner.candidate.column,
            scope_key=winner.candidate.scope_key,
            fingerprint=winner.candidate.fingerprint,
            title=winner.candidate.title,
            summary=winner.candidate.summary,
            severity=winner.candidate.severity,
            confidence=winner.candidate.confidence,
            affected_records=winner.candidate.affected_records,
            facts=tuple(facts),
            interpretation=winner.candidate.interpretation,
            hypotheses=tuple(hypotheses),
            evidence_refs=tuple(evidence),
        )
        merged.append(PrioritizedCandidate(candidate=combined, priority=winner.priority))
    return merged


def assemble_findings(
    merged: Sequence[PrioritizedCandidate], run_id: str
) -> Tuple[Finding, ...]:
    """Sort by ``(-priority, fingerprint)`` and stamp frozen §4.2 Findings.

    ``finding_id`` is a run-local label assigned after the deterministic sort
    (the cross-run identity is ``fingerprint``, ADR-016). MVP workflow is always
    ``null`` (CONTRACTS §4.2 / §4.5).
    """
    ordered = sorted(merged, key=lambda it: (-it.priority, it.candidate.fingerprint))
    findings: List[Finding] = []
    for idx, item in enumerate(ordered, start=1):
        c = item.candidate
        scope_kwargs: Dict[str, object] = {"column": c.column}
        if c.affected_records is not None:
            scope_kwargs["affected_records"] = c.affected_records
        findings.append(
            Finding(
                schema_version=SCHEMA_VERSION,
                finding_id=f"F-{idx:03d}",
                fingerprint=c.fingerprint,
                run_id=run_id,
                title=c.title,
                summary=c.summary,
                category=c.category,
                severity=c.severity,
                confidence=c.confidence,
                scope=FindingScope(**scope_kwargs),
                facts=list(c.facts),
                interpretation=c.interpretation,
                hypotheses=list(c.hypotheses),
                evidence_refs=list(c.evidence_refs),
                related_finding_ids=[],
                recommendation_ids=[],
                workflow=None,
            )
        )
    return tuple(findings)


def run_pipeline(
    candidates: Sequence[Candidate],
    run_id: str,
    row_count: Optional[int],
    critical_columns: Sequence[str],
) -> Tuple[Finding, ...]:
    """prioritize → dedup/merge → assemble (ENGINE §6 steps 3–5)."""
    prioritized = [
        PrioritizedCandidate(c, priority_of(c, row_count, critical_columns))
        for c in candidates
    ]
    merged = dedup_and_merge(prioritized)
    return assemble_findings(merged, run_id)


__all__ = [
    "PrioritizedCandidate",
    "priority_of",
    "dedup_and_merge",
    "assemble_findings",
    "run_pipeline",
]
