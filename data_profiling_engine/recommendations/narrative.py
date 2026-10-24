"""Narrative assembly — human-readable recommendation text built strictly from
the evidence a finding already carries (M2-T9, ENGINE §7).

``narrative.py assembles human-readable text strictly from available evidence``
— so this module never introduces a number or a cause of its own. It fills the
``{column}`` slot of a :class:`~data_profiling_engine.recommendations.templates.
RecommendationTemplate` and appends the *verbatim* evidence refs the finding was
built on (AGENTS §3: never present an unsupported statement as proven). The
rationale cites exactly those refs, so every recommendation is traceable back to
a real metric / rule (CONTRACTS §4.3).
"""
from __future__ import annotations

from typing import List, Tuple

from data_profiling_engine.recommendations.templates import (
    DEFAULT_LANGUAGE,
    RecommendationTemplate,
)
from data_profiling_engine.schemas.models import Finding

# Dataset-level subject label per narrative language (ADR-018). A real column
# name is always substituted verbatim; only the *fallback* for a scope with no
# single column is localized (never a fabricated column name).
_DATASET_LABEL = {"en": "the dataset", "id": "dataset ini"}


def subject_column(finding: Finding, language: str = DEFAULT_LANGUAGE) -> str:
    """The column the recommendation acts on, or a dataset-level label when the
    finding scope has no single column (never fabricates a column name)."""
    column = finding.scope.column if finding.scope is not None else None
    return column or _DATASET_LABEL.get(language, _DATASET_LABEL[DEFAULT_LANGUAGE])


def compose_action(template: RecommendationTemplate, column: str) -> str:
    """Imperative next step + its investigation probe (ENGINE §7 lists both).

    The probe is folded into the action so a single ``action`` string carries the
    concrete step *and* how to carry it out — e.g. completeness's "group the
    missing rate by status / source / period".
    """
    action = template.action.format(column=column)
    probe = template.probe.format(column=column)
    return f"{action} {probe}"


def compose_rationale(
    template: RecommendationTemplate, column: str, evidence_refs: Tuple[str, ...]
) -> str:
    """Why the action is proposed, grounded in and quoting the *existing*
    evidence refs (CONTRACTS §7: rationale must be traceable to evidence)."""
    body = template.rationale.format(column=column)
    return f"{body} Evidence: {', '.join(evidence_refs)}."


def compose_target(template: RecommendationTemplate, column: str) -> str:
    """Observable end-state once the action lands (evidence substitution only)."""
    return template.target_improvement.format(column=column)


def compose_do_not(template: RecommendationTemplate, column: str) -> List[str]:
    """Anti-pattern guardrails (e.g. 'Do not fill empty values with zero')."""
    return [guard.format(column=column) for guard in template.do_not]


__all__ = [
    "subject_column",
    "compose_action",
    "compose_rationale",
    "compose_target",
    "compose_do_not",
]
