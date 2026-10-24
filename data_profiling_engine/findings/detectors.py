"""Finding detectors — candidates from existing evidence (M2-T8, ENGINE §6.1).

A detector turns already-computed, already-provenanced evidence into a
:class:`Candidate` finding. It **never invents a number** (AGENTS §3): every
fact carries a ``metric_ref`` + the ``Provenance`` that produced it, and every
candidate is the product of one of the two ENGINE §6.1 sources —

1. :func:`detect_from_quality_checks` — a quality ``FAIL``/``WARN``
   :class:`QualityCheckResult` (§4.1) becomes a finding with the rule + metric
   evidence attached. ``PASS`` produces nothing; ``ERROR`` produces nothing
   either — an un-executable check is *not* a proven data problem (FAIL ≠ ERROR,
   AGENTS §3), it is surfaced as a run error elsewhere.
2. :func:`detect_from_profiles` — a *profiling extreme with no rule behind it*
   (constant column, near-total missingness, single dominant category,
   duplicated key candidates) becomes a ``WARN``-style finding, always carrying
   the ColumnProfile metric ref it was read from (ENGINE §6.1).

Candidates are ordered / de-duplicated / assembled into contract
:class:`Finding` objects downstream in :mod:`findings.pipeline`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from data_profiling_engine.findings.fingerprinting import (
    build_scope_key,
    compute_fingerprint,
)
from data_profiling_engine.schemas.models import (
    CheckStatus,
    ColumnProfile,
    FindingConfidence,
    FindingFact,
    FindingHypothesis,
    HypothesisStatus,
    MetricMethod,
    Provenance,
    QualityCheckResult,
    QualityDimension,
    Severity,
)

# ---------------------------------------------------------------------------
# MVP detector thresholds (documented heuristics, no per-domain code path —
# AGENTS §5). A profile-extreme detector fires only past these bounds; they are
# *review* flags (WARN), never hard failures.
# ---------------------------------------------------------------------------

#: ``missingness.missing_rate`` at/above this share ⇒ "near-total missingness".
NEAR_TOTAL_MISSINGNESS = 0.90
#: Largest ``categorical`` bucket share at/above this ⇒ "single dominant category".
DOMINANT_CATEGORY_SHARE = 0.95

# Detector identity tokens (ride the fingerprint, §4.4).
RULE_DETECTOR_ID = "quality_rule"
_MISSINGNESS_DETECTOR_ID = "profile_near_total_missingness"
_CONSTANT_DETECTOR_ID = "profile_constant_column"
_DOMINANT_DETECTOR_ID = "profile_dominant_category"
_DUPLICATE_DETECTOR_ID = "profile_duplicate_key_candidates"

# method ∈ {exact, approximate, sampled} ⇒ confidence (ENGINE §6.2: confidence
# reflects method + evidence completeness). not_computed never reaches a fact.
_METHOD_CONFIDENCE = {
    MetricMethod.EXACT.value: FindingConfidence.HIGH,
    MetricMethod.APPROXIMATE.value: FindingConfidence.MEDIUM,
    MetricMethod.SAMPLED.value: FindingConfidence.LOW,
}

# Dimension-keyed review framing for rule-derived findings. These are the only
# interpretive strings the engine attaches; each stays in its structurally
# separate slot (facts / interpretation / hypotheses — CONTRACTS §7), and every
# hypothesis is emitted `unverified` (MVP lock, §4.2).
_DIMENSION_INTERPRETATION = {
    QualityDimension.COMPLETENESS: "Absent values bias counts and aggregates that rely on this field.",
    QualityDimension.UNIQUENESS: "Duplicated key values can double-count records joined on this field.",
    QualityDimension.VALIDITY: "Values outside the expected form can fail downstream logic.",
    QualityDimension.CONSISTENCY: "Cross-field disagreement suggests a process or mapping issue.",
    QualityDimension.TIMELINESS: "Temporal gaps or staleness can make period analysis unreliable.",
    QualityDimension.ACCURACY_FLAGGED: "This is a review signal, not a proven error; verify against source intent.",
}
_DIMENSION_PROBE = {
    QualityDimension.COMPLETENESS: "group the missing rate by status / source / period",
    QualityDimension.UNIQUENESS: "inspect the duplicated key values and their source rows",
    QualityDimension.VALIDITY: "sample the offending rows and compare against the intended domain",
    QualityDimension.CONSISTENCY: "compare the paired fields across the violating rows",
    QualityDimension.TIMELINESS: "chart volume over time to locate the gap or stale window",
    QualityDimension.ACCURACY_FLAGGED: "confirm the pattern against the business definition before acting",
}


@dataclass(frozen=True)
class Candidate:
    """One not-yet-assembled finding: fingerprint identity + content payload.

    The pipeline dedups/merges on ``(column, category)`` and ranks by priority;
    the ``fingerprint`` is computed eagerly here so identity is available for
    the cross-run contract (ADR-016) regardless of assembly outcome.
    """

    detector_id: str
    normalized_measure: str
    category: QualityDimension
    column: Optional[str]
    scope_key: str
    fingerprint: str
    title: str
    summary: str
    severity: Severity
    confidence: FindingConfidence
    affected_records: Optional[int]
    facts: Tuple[FindingFact, ...]
    interpretation: Optional[str]
    hypotheses: Tuple[FindingHypothesis, ...]
    evidence_refs: Tuple[str, ...]


def _confidence_of(provenance: Provenance) -> FindingConfidence:
    return _METHOD_CONFIDENCE.get(
        provenance.method.value
        if hasattr(provenance.method, "value")
        else str(provenance.method),
        FindingConfidence.MEDIUM,
    )


def _val(field) -> str:
    """Read an enum-or-string field as its string value. Under
    ``use_enum_values=True`` a persisted contract field (dimension / status /
    operator) is a *plain str* at runtime, so ``.value`` would fail — take the
    attribute only when a real enum reached us (STATE GOTCHA M2-T2/M2-T7)."""
    return field.value if hasattr(field, "value") else str(field)


def _fmt(value) -> str:
    """Compact, honest number text for a summary — never a fabricated value."""
    if isinstance(value, float):
        return f"{round(value, 6):g}"
    return str(value)


def _pct(value: float) -> str:
    return f"{round(value * 100, 1):g}%"


def _metric_ref(column: Optional[str], measure: str) -> str:
    return f"metric:{column}.{measure}" if column else f"metric:{measure}"


def detect_from_quality_checks(
    dataset_id: str, checks: Tuple[QualityCheckResult, ...]
) -> List[Candidate]:
    """FAIL / WARN rule results ⇒ findings (ENGINE §6.1 'from quality results').

    ``observed`` is always present on a FAIL/WARN result (the executor only
    leaves it null on ERROR, which is skipped), so each fact carries the exact
    provenance the rule was measured with. Identity rides the ``rule_id`` (a
    stable config identifier), not the changing violation count.
    """
    out: List[Candidate] = []
    for c in checks:
        if c.status not in (CheckStatus.FAIL, CheckStatus.WARN):
            continue  # PASS ⇒ nothing; ERROR ⇒ not a proven problem (never a finding)
        observed = c.observed
        if observed is None:  # pragma: no cover - defensive; FAIL/WARN always carry one
            continue
        provenance = observed.provenance
        metric_ref = f"metric:{c.rule_id}"
        rule_ref = f"rule:{c.rule_id}"

        threshold = c.threshold
        summary = _rule_summary(c, threshold)
        out.append(
            Candidate(
                detector_id=RULE_DETECTOR_ID,
                normalized_measure=c.rule_id,
                category=c.dimension,
                column=c.column,
                scope_key=build_scope_key(dataset_id, c.column),
                fingerprint=compute_fingerprint(
                    _val(c.dimension), build_scope_key(dataset_id, c.column),
                    RULE_DETECTOR_ID, c.rule_id,
                ),
                title=_rule_title(c),
                summary=summary,
                severity=c.severity,
                confidence=_confidence_of(provenance),
                affected_records=c.failing_record_count,
                facts=(FindingFact(
                    metric_ref=metric_ref,
                    value=observed.value,
                    provenance=provenance,
                ),),
                interpretation=_DIMENSION_INTERPRETATION.get(c.dimension),
                hypotheses=(_probe_hypothesis(c.dimension),),
                evidence_refs=(metric_ref, rule_ref),
            )
        )
    return out


def _rule_title(c: QualityCheckResult) -> str:
    status = "flagged for review" if c.status == CheckStatus.WARN else "violated"
    col = c.column or "dataset"
    return f"{col} {_val(c.dimension)} rule {status}"


def _rule_summary(c: QualityCheckResult, threshold) -> str:
    observed = c.observed
    value_txt = _fmt(observed.value) if observed is not None else "n/a"
    col = c.column or "the dataset"
    if threshold is not None and threshold.value is not None:
        upper = (
            f" to {_fmt(threshold.upper)}" if threshold.upper is not None else ""
        )
        bound = f"{_val(threshold.operator)} {_fmt(threshold.value)}{upper}"
        rate_txt = (
            _pct(observed.value)
            if isinstance(observed.value, float) and _val(threshold.operator) in ("<=", "<")
            else value_txt
        )
        return (
            f"{col} measured {rate_txt} against rule '{c.rule_id}' "
            f"(threshold {bound})."
        )
    return (
        f"Rule '{c.rule_id}' on {col} measured {value_txt}; status {_val(c.status)}."
    )


def _probe_hypothesis(dimension: QualityDimension) -> FindingHypothesis:
    return FindingHypothesis(
        text=f"The {_val(dimension)} issue may cluster by source, status, or period.",
        status=HypothesisStatus.UNVERIFIED,
        suggested_probe=_DIMENSION_PROBE.get(dimension),
    )


def detect_from_profiles(
    dataset_id: str,
    profiles: Tuple[ColumnProfile, ...],
    duplicate_group_counts: Optional[Dict[str, int]] = None,
) -> List[Candidate]:
    """ColumnProfile extremes with *no rule behind them* ⇒ WARN review findings
    (ENGINE §6.1). Every candidate cites the metric ref it was read from.
    ``duplicate_group_counts`` is the M2-T5 uniqueness auxiliary (contract_target
    ``None``); a column with duplicated key values and no other uniqueness signal
    becomes an informational near-key finding (dedup merges it with any
    uniqueness rule/constant finding on the same column downstream)."""
    dup_counts = duplicate_group_counts or {}
    out: List[Candidate] = []
    for p in profiles:
        for cand in (
            _detect_missingness(dataset_id, p),
            _detect_constant(dataset_id, p),
            _detect_dominant_category(dataset_id, p),
            _detect_duplicates(dataset_id, p, dup_counts.get(p.column, 0)),
        ):
            if cand is not None:
                out.append(cand)
    return out


def _warn_candidate(
    dataset_id: str, p: ColumnProfile, detector_id: str, normalized_measure: str,
    category: QualityDimension, summary: str, metric_ref: str, value,
    provenance: Provenance, affected_records: Optional[int],
    confidence: FindingConfidence,
) -> Candidate:
    scope_key = build_scope_key(dataset_id, p.column)
    return Candidate(
        detector_id=detector_id,
        normalized_measure=normalized_measure,
        category=category,
        column=p.column,
        scope_key=scope_key,
        fingerprint=compute_fingerprint(
            category.value, scope_key, detector_id, normalized_measure
        ),
        title=f"{p.column}: {summary.split('.')[0]}",
        summary=summary,
        severity=Severity.WARNING,
        confidence=confidence,
        affected_records=affected_records,
        facts=(FindingFact(metric_ref=metric_ref, value=value, provenance=provenance),),
        interpretation=_DIMENSION_INTERPRETATION.get(category),
        hypotheses=(_probe_hypothesis(category),),
        evidence_refs=(metric_ref,),
    )


def _method(provenance: Provenance) -> str:
    return provenance.method.value if hasattr(provenance.method, "value") else str(provenance.method)


def _detect_missingness(dataset_id: str, p: ColumnProfile) -> Optional[Candidate]:
    m = p.missingness
    if m is None:
        return None
    mv = m.missing_rate
    if mv.value is None or _method(mv.provenance) == MetricMethod.NOT_COMPUTED.value:
        return None
    rate = float(mv.value)
    if rate < NEAR_TOTAL_MISSINGNESS:
        return None
    affected = m.null_count + m.blank_count + m.sentinel_count
    measure = "missingness.missing_rate"
    metric_ref = _metric_ref(p.column, measure)
    summary = (
        f"{_pct(rate)} of {p.column} is missing, above the {_pct(NEAR_TOTAL_MISSINGNESS)} "
        f"near-total review threshold."
    )
    return _warn_candidate(
        dataset_id, p, _MISSINGNESS_DETECTOR_ID, measure,
        QualityDimension.COMPLETENESS, summary, metric_ref, rate, mv.provenance,
        affected, _confidence_of(mv.provenance),
    )


def _detect_constant(dataset_id: str, p: ColumnProfile) -> Optional[Candidate]:
    u = p.uniqueness
    if u is None:
        return None
    dc = u.distinct_count
    if dc.value is None or _method(dc.provenance) == MetricMethod.NOT_COMPUTED.value:
        return None
    denom = dc.denominator
    # "constant" = exactly one distinct value across ≥2 present rows; a single-row
    # or empty relation is not flagged (no discrimination signal yet).
    if int(dc.value) != 1 or denom is None or denom < 2:
        return None
    measure = "uniqueness.distinct_count"
    metric_ref = _metric_ref(p.column, measure)
    summary = (
        f"{p.column} has a single distinct value across {denom} rows; it carries no "
        f"discriminating information."
    )
    return _warn_candidate(
        dataset_id, p, _CONSTANT_DETECTOR_ID, measure,
        QualityDimension.UNIQUENESS, summary, metric_ref, dc.value, dc.provenance,
        denom, _confidence_of(dc.provenance),
    )


def _detect_dominant_category(dataset_id: str, p: ColumnProfile) -> Optional[Candidate]:
    cat = p.categorical
    if cat is None or not cat.categories or not cat.frequencies:
        return None
    total = sum(int(f) for f in cat.frequencies)
    if total <= 0:
        return None
    top = max(int(f) for f in cat.frequencies)
    share = top / total
    if share < DOMINANT_CATEGORY_SHARE:
        return None
    # frequencies were an exact GROUP BY enumeration (M2-T5), but the block is
    # top-K bounded, so evidence completeness is capped ⇒ MEDIUM confidence.
    provenance = Provenance(method=MetricMethod.EXACT)
    measure = "categorical.frequencies"
    metric_ref = _metric_ref(p.column, measure)
    summary = (
        f"{_pct(share)} of {p.column}'s non-null values fall in a single category; "
        f"near-constant distribution warrants review."
    )
    return _warn_candidate(
        dataset_id, p, _DOMINANT_DETECTOR_ID, measure,
        QualityDimension.ACCURACY_FLAGGED, summary, metric_ref, round(share, 6),
        provenance, top, FindingConfidence.MEDIUM,
    )


def _detect_duplicates(
    dataset_id: str, p: ColumnProfile, dup_count: int
) -> Optional[Candidate]:
    if dup_count <= 0:
        return None
    # The auxiliary duplicate count was an exact per-group computation (M2-T5);
    # no MetricValue provenance carrier exists for it, so stamp exact + reason.
    provenance = Provenance(
        method=MetricMethod.EXACT,
        reason="duplicate-key grouping count (uniqueness auxiliary)",
    )
    measure = "uniqueness.duplicate_group_count"
    metric_ref = _metric_ref(p.column, measure)
    summary = (
        f"{p.column} has {dup_count} duplicated key values; likely not a unique "
        f"identifier."
    )
    return _warn_candidate(
        dataset_id, p, _DUPLICATE_DETECTOR_ID, measure,
        QualityDimension.UNIQUENESS, summary, metric_ref, dup_count, provenance,
        dup_count, FindingConfidence.MEDIUM,
    )


__all__ = [
    "Candidate",
    "detect_from_quality_checks",
    "detect_from_profiles",
    "NEAR_TOTAL_MISSINGNESS",
    "DOMINANT_CATEGORY_SHARE",
]
