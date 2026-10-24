"""Rule registry — the closed MVP catalog of quality-rule types (M2-T7,
ENGINE §5.1; ARCHITECTURE §3 ``quality/`` = "rule registry, rule compiler,
executor").

A rule is a *declarative* definition supplied by ``dataset_config.quality_checks``
(CONTRACTS §9 :class:`DatasetConfigRule`). This module owns only the **metadata**
that tells the compiler (:mod:`data_profiling_engine.quality.compiler`) how to
turn a rule *type* into one SQL statement and how to read its result — the
behaviour is metadata-driven, never ``check_<domain>()`` code (AGENTS §5).

ENGINE §5.1 semantics captured per type:

- ``measure``: how a rule's verdict is read off the compiled ``(violations,
  total)`` aggregate — a raw ``count`` (PASS iff 0 violations), a bounded
  ``rate`` (PASS iff ``violations/total <= value``), or an ``outlier`` count
  (WARN-only, §5.3 "never hard-fail").
- ``warn_only``: types that raise review candidates rather than failures
  (``outlier_scan`` §5.1 "→ WARN"; a ``business_note``/``warning``-severity
  rule adds WARN at execution, §5.3, and is not a registry property).
- ``phase``: ``referential_integrity`` / ``date_order`` are declared in the
  config catalog but are Phase-2/deep — they *compile to nothing* in MVP and
  surface as ``ERROR`` (not executed), never silently dropped (§5.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from data_profiling_engine.schemas.models import QualityDimension, RuleType


@dataclass(frozen=True)
class RuleDefinition:
    """Static description of one §5.1 rule type (compiler input, no data)."""

    rule_type: RuleType
    default_dimension: QualityDimension
    measure: str                     # 'count' | 'rate' | 'outlier'
    param_kind: str                  # 'none' | 'bound' | 'bounds' | 'domain' | 'condition'
    warn_only: bool                  # True → positive result is WARN, never FAIL (§5.3)
    mvp_active: bool                 # False → Phase-2/deep; not executed in MVP
    summary: str


#: ENGINE §5.1 MVP rule catalog. ``default_dimension`` mirrors the §5.1
#: "Dimension" column; the config rule's own ``dimension`` (required, §9) always
#: wins — this default is documentation + a fallback only, never an override.
_RULE_CATALOG: Dict[RuleType, RuleDefinition] = {
    RuleType.NOT_NULL: RuleDefinition(
        RuleType.NOT_NULL, QualityDimension.COMPLETENESS, "count", "none",
        False, True, "column has no nulls (or count below an optional max)",
    ),
    RuleType.UNIQUE: RuleDefinition(
        RuleType.UNIQUE, QualityDimension.UNIQUENESS, "count", "none",
        False, True, "non-null column values are unique",
    ),
    RuleType.MISSING_RATE_MAX: RuleDefinition(
        RuleType.MISSING_RATE_MAX, QualityDimension.COMPLETENESS, "rate", "bound",
        False, True, "missing rate (nulls+blanks+sentinels) <= threshold",
    ),
    RuleType.GREATER_THAN_OR_EQUAL: RuleDefinition(
        RuleType.GREATER_THAN_OR_EQUAL, QualityDimension.VALIDITY, "count", "bound",
        False, True, "every value >= a lower bound",
    ),
    RuleType.LESS_THAN_OR_EQUAL: RuleDefinition(
        RuleType.LESS_THAN_OR_EQUAL, QualityDimension.VALIDITY, "count", "bound",
        False, True, "every value <= an upper bound",
    ),
    RuleType.RANGE: RuleDefinition(
        RuleType.RANGE, QualityDimension.VALIDITY, "count", "bounds",
        False, True, "every value within [low, high]",
    ),
    RuleType.VALID_DOMAIN: RuleDefinition(
        RuleType.VALID_DOMAIN, QualityDimension.VALIDITY, "count", "domain",
        False, True, "values are a subset of an allowed set",
    ),
    RuleType.TYPE_VALID: RuleDefinition(
        RuleType.TYPE_VALID, QualityDimension.VALIDITY, "count", "none",
        False, True, "values parse as the declared logical type",
    ),
    RuleType.CONDITIONAL_NOT_NULL: RuleDefinition(
        RuleType.CONDITIONAL_NOT_NULL, QualityDimension.CONSISTENCY, "count", "condition",
        False, True, "not-null when a condition column matches",
    ),
    RuleType.OUTLIER_SCAN: RuleDefinition(
        RuleType.OUTLIER_SCAN, QualityDimension.ACCURACY_FLAGGED, "outlier", "none",
        True, True, "Tukey IQR candidate count beyond fences (→ WARN, §5.1)",
    ),
    RuleType.REFERENTIAL_INTEGRITY: RuleDefinition(
        RuleType.REFERENTIAL_INTEGRITY, QualityDimension.CONSISTENCY, "count", "none",
        False, False, "values exist in another table (Phase 2, §5.1)",
    ),
    RuleType.DATE_ORDER: RuleDefinition(
        RuleType.DATE_ORDER, QualityDimension.CONSISTENCY, "count", "none",
        False, False, "cross-column temporal ordering (Phase 2/deep, §5.1)",
    ),
}


class RuleRegistryError(Exception):
    """Registry-lookup failure (an unknown rule type reaching the compiler).
    The dataset-config schema is the pre-execution gate (CONTRACTS §9
    ``additionalProperties:false`` + a closed ``rule`` enum), so this only fires
    on a direct API misuse, never on a validated config."""


def lookup(rule_type: RuleType) -> RuleDefinition:
    try:
        return _RULE_CATALOG[rule_type]
    except KeyError as exc:  # pragma: no cover - config schema closes the enum
        raise RuleRegistryError(f"unknown rule type: {rule_type!r}") from exc


def is_active(rule_type: RuleType) -> bool:
    """MVP-executable (False for the Phase-2/deep types, which surface ERROR)."""
    return lookup(rule_type).mvp_active


def default_dimension(rule_type: RuleType) -> QualityDimension:
    return lookup(rule_type).default_dimension


def catalog() -> Dict[RuleType, RuleDefinition]:
    """Immutable snapshot of the §5.1 catalog (for tests / introspection)."""
    return dict(_RULE_CATALOG)


__all__ = [
    "RuleDefinition",
    "RuleRegistryError",
    "lookup",
    "is_active",
    "default_dimension",
    "catalog",
]
