"""Recommendation engine — the *acting* layer of the
measure ≠ judge ≠ interpret ≠ act pipeline (M2-T9, ENGINE §7; CONTRACTS §4.3).

It turns each M2-T8 :class:`Finding` into a rule-based, evidence-citing
:class:`Recommendation` and wires the back-reference onto the finding
(``Finding.recommendation_ids``, which M2-T8 deliberately left empty). Nothing
here re-measures or invents: recommendations are built **only** from existing
findings, each carries ``reason_type`` and the finding's evidence refs verbatim,
and a suggestion with no proven cause is phrased as an
``business_context_required`` investigation question (AGENTS §3, CONTRACTS §7).
AI/LLM rephrasing is out of MVP (BLUEPRINT Q3).

Public surface::

    from data_profiling_engine.recommendations import build_recommendations

    result = build_recommendations(findings)          # findings = M2-T8 output
    for rec in result.recommendations:                # R-001.. , §4.3-valid
        ...
    for finding in result.findings:                   # recommendation_ids wired
        ...

Submodules: :mod:`recommendations.templates` (rule catalog, §7),
:mod:`recommendations.narrative` (evidence-grounded text),
:mod:`recommendations.rule_based` (finding → Recommendation executor).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from data_profiling_engine.recommendations.rule_based import recommendation_for_finding
from data_profiling_engine.recommendations.templates import DEFAULT_LANGUAGE
from data_profiling_engine.schemas.models import Finding, Recommendation


@dataclass(frozen=True)
class RecommendationsResult:
    """Outcome of one recommendation pass.

    ``recommendations`` are ordered to match the input findings (which arrive
    already prioritized from M2-T8), so ``R-001`` answers the highest-priority
    ``F-001``. ``findings`` are the same findings re-emitted with their
    ``recommendation_ids`` back-reference populated.
    """

    recommendations: Tuple[Recommendation, ...]
    findings: Tuple[Finding, ...]


def _wire(finding: Finding, recommendation_ids: Sequence[str]) -> Finding:
    """Return a copy of ``finding`` with ``recommendation_ids`` set.

    Rebuilt through ``model_validate`` on the canonical dump so the update
    registers in ``fields_set`` (the strict ContractModel only emits a key it
    was explicitly given) and the result is guaranteed schema-valid again.
    """
    data = finding.model_dump()
    data["recommendation_ids"] = list(recommendation_ids)
    return Finding.model_validate(data)


def build_recommendations(
    findings: Sequence[Finding], language: str = DEFAULT_LANGUAGE
) -> RecommendationsResult:
    """Generate one evidence-citing recommendation per finding and wire the
    finding → recommendation back-reference (ENGINE §6 step 5 / §7).

    ``recommendation_id`` is a deterministic run-local ``R-NNN`` assigned in the
    order the (already-prioritized) findings arrive, so the mapping is stable
    across reruns of the same evidence.

    ``language`` (ADR-018 / BLUEPRINT Q1) selects the narrative template language
    — ``"en"`` (default, shipped) or ``"id"`` (Indonesian). It only changes the
    human-readable strings; ids, evidence refs, ``reason_type`` and the
    Recommendation shape are identical across languages.
    """
    recommendations: list[Recommendation] = []
    wired: list[Finding] = []
    for idx, finding in enumerate(findings, start=1):
        rid = f"R-{idx:03d}"
        rec = recommendation_for_finding(finding, rid, language)
        recommendations.append(rec)
        wired.append(_wire(finding, [rid]))
    return RecommendationsResult(
        recommendations=tuple(recommendations), findings=tuple(wired)
    )


__all__ = [
    "build_recommendations",
    "RecommendationsResult",
    "recommendation_for_finding",
]
