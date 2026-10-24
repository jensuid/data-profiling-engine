"""Rule-based recommendation executor — maps each M2-T8 :class:`Finding` to a
contract :class:`Recommendation` via the template catalog (M2-T9, ENGINE §7;
CONTRACTS §4.3).

The engine **consumes only the findings** the interpretation layer produced and
never re-reads metrics or invents a number (AGENTS §3). For one finding it:

1. classifies the finding to a template :term:`kind` (``classify_finding_kind``),
2. substitutes only the ``{column}`` slot (values stay in the cited evidence),
3. carries the finding's ``evidence_refs`` **verbatim** onto the recommendation
   so ``reason_type=evidence`` always points at real metric/rule refs
   (CONTRACTS §4.3 — "recommendations must reference evidence"; a review signal
   with no proven cause is instead marked ``business_context_required``),
4. stamps ``status="proposed"`` (MVP; acceptance is Final App workflow).
"""
from __future__ import annotations

from typing import List

from data_profiling_engine.discovery.inference import SCHEMA_VERSION
from data_profiling_engine.recommendations import narrative
from data_profiling_engine.recommendations.templates import (
    DEFAULT_LANGUAGE,
    classify_finding_kind,
    templates_for_language,
)
from data_profiling_engine.schemas.models import (
    Finding,
    ReasonType,
    Recommendation,
    RecommendationStatus,
)


def recommendation_for_finding(
    finding: Finding,
    recommendation_id: str,
    language: str = DEFAULT_LANGUAGE,
) -> Recommendation:
    """Build the one :class:`Recommendation` that acts on ``finding``.

    ``evidence_refs`` is the finding's own ref list copied verbatim — the
    recommendation cites exactly the evidence the finding rests on, adding (or
    altering) nothing. ``language`` selects the narrative catalog (ADR-018 /
    BLUEPRINT Q1: "en" default, "id" Indonesian) — it changes only the
    human-readable strings, never the Recommendation shape (§4.3).
    """
    template = templates_for_language(language)[classify_finding_kind(finding)]
    column = narrative.subject_column(finding, language)
    evidence: List[str] = list(finding.evidence_refs)

    reason_type = template.reason_type
    # CONTRACTS §4.3: `evidence` REQUIRES non-empty refs. A finding always
    # carries ≥1 (§4.2 minItems), but if a review template somehow had none to
    # cite it is not "evidence" — degrade to business_context_required rather
    # than emit an unsupported claim.
    if reason_type == ReasonType.EVIDENCE and not evidence:
        reason_type = ReasonType.BUSINESS_CONTEXT_REQUIRED

    return Recommendation(
        schema_version=SCHEMA_VERSION,
        recommendation_id=recommendation_id,
        finding_id=finding.finding_id,
        action=narrative.compose_action(template, column),
        rationale=narrative.compose_rationale(template, column, tuple(evidence)),
        reason_type=reason_type,
        target_improvement=narrative.compose_target(template, column),
        do_not=narrative.compose_do_not(template, column),
        evidence_refs=evidence,
        status=RecommendationStatus.PROPOSED,
    )


__all__ = ["recommendation_for_finding"]
