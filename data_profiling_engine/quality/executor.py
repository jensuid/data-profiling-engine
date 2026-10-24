"""Quality rule executor — ENGINE §5.3 "compile → execute → PASS / FAIL / WARN,
or ERROR when execution raises" (M2-T7).

Public entry point :func:`run_quality_checks` walks ``dataset_config.quality_checks``
(CONTRACTS §9), compiles each rule with :func:`~quality.compiler.compile_rule`,
runs the one bound statement against the relation, and emits a
:class:`QualityCheckResult` (§4.1) per rule. Behavioural obligations:

- **FAIL ≠ ERROR** (AGENTS §3, §5.3): a rule that cannot be compiled or executed
  (absent column, malformed parameter, non-numeric target, broken SQL, a
  Phase-2/deep type) is ``status=ERROR`` with an ``error`` object — the engine
  has *not* proven the rule holds. It is never a silent ``FAIL`` and never
  dropped (§6.4 "successful sections always preserved").
- **WARN never hard-fails** (§5.3): ``outlier_scan`` and any rule carrying a
  ``business_note`` or ``warning`` severity that *does* violate become ``WARN``
  review candidates rather than ``FAIL``.
- **Per-task isolation** (§6.4): one rule raising does not abort the run — the
  failure lands on that result AND in the returned ``error_log`` rows
  (§8 ``RULE_EXEC_FAILED``); sibling rules keep executing.
- **Honest provenance** (ADR-015): every non-ERROR observed value is an
  ``exact`` one-row aggregate (rules are not sampled until M2-T10 wires the
  window), with the scanned population as ``denominator``.
- **Row-reference evidence** (SECURITY §4): ``sample_row_refs`` carry
  ``row:N`` connector row-ids, never raw column values.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import duckdb

from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery.types import DiscoveryReport, LogicalType
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.quality.compiler import (
    CompiledRule,
    RuleCompileError,
    compile_rule,
)
from data_profiling_engine.schemas.models import (
    CheckError,
    CheckEvidence,
    CheckStatus,
    CheckThreshold,
    DatasetConfig,
    DatasetConfigRule,
    MetricMethod,
    MetricValue,
    Provenance,
    QualityCheckResult,
    RunErrorEntry,
    Severity,
    ThresholdOperator,
)
from data_profiling_engine.discovery.inference import SCHEMA_VERSION

#: MVP rule version — the frozen §9 config rule carries no version field, so
#: every emitted result stamps the ENGINE §5.1 catalog version (a string; the
#: §4.1 exemplar shows ``rule_version: "1"``).
RULE_VERSION = "1"

#: Bounded, non-echoing error text (SECURITY §5 — a DuckDB message can quote an
#: offending value; we never surface raw dataset content in an error).
_MAX_REASON_LEN = 300


@dataclass(frozen=True)
class QualityRunResult:
    """Outcome of one quality pass: a result per rule (§4.1) plus §6.4 error
    rows for the run record. Order follows ``config.quality_checks``."""

    checks: Tuple[QualityCheckResult, ...]
    errors: Tuple[RunErrorEntry, ...] = ()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


def _reason(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:_MAX_REASON_LEN]


def _warn_policy(rule: DatasetConfigRule, compiled: CompiledRule) -> bool:
    """§5.3: WARN-only when the type is inherently review-flagging, the author
    attached a ``business_note`` (semantics-aware negative), or the severity is
    itself ``warning``. ``rule.severity`` is a plain string under
    use_enum_values, so compare on the enum value (STATE GOTCHA M2-T2)."""
    return bool(
        compiled.warn_only
        or (rule.business_note and rule.business_note.strip())
        or rule.severity == Severity.WARNING.value
    )


def _build_observed(measure: str, violations: int, total: int, max_rate: Optional[float]):
    """MetricValue + failing_record_count for a successfully executed rule.
    ``value`` is a raw violation count for count/outlier measures, or the
    missing rate for a rate measure (never presented without its denominator)."""
    denom = int(total)
    if measure == "rate":
        rate = 0.0 if denom == 0 else (violations / denom)
        value: Any = rate
    else:
        value = int(violations)
    mv = MetricValue(
        value=value,
        provenance=Provenance(method=MetricMethod.EXACT),
        denominator=denom,
    )
    return mv, int(violations)


def _evidence(total: int, refs: List[Any]) -> CheckEvidence:
    row_refs = [f"row:{int(r)}" for r in (refs or [])]
    kwargs: Dict[str, Any] = {"denominator": int(total)}
    if row_refs:
        kwargs["sample_row_refs"] = row_refs
    return CheckEvidence(**kwargs)


def _decide_status(
    measure: str, violations: int, total: int, max_rate: Optional[float], warn: bool
) -> CheckStatus:
    """§5.3 verdict: not-violating ⇒ PASS; violating ⇒ FAIL, or WARN when the
    rule is review-only (outlier / business_note / warning severity)."""
    if measure == "rate":
        rate = 0.0 if total == 0 else (violations / total)
        violated = max_rate is not None and rate > max_rate
    else:
        violated = violations > 0
    if not violated:
        return CheckStatus.PASS
    return CheckStatus.WARN if warn else CheckStatus.FAIL


def _error_result(
    rule: DatasetConfigRule, run_id: str, executed_at: str,
    message: str, threshold: Optional[CheckThreshold] = None,
) -> QualityCheckResult:
    """ERROR ⇔ a populated ``error`` and NO observed value (schema §4.1 coupling;
    FAIL≠ERROR). ``threshold`` is carried when the rule still compiled."""
    return QualityCheckResult(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        rule_id=rule.id,
        rule_version=RULE_VERSION,
        dimension=rule.dimension,
        status=CheckStatus.ERROR,
        severity=rule.severity,
        column=rule.column,
        observed=None,
        threshold=threshold,
        failing_record_count=None,
        evidence=None,
        error=CheckError(code=ErrorCode.RULE_EXEC_FAILED.value, message=message),
        executed_at=executed_at,
    )


def _run_one(
    con: duckdb.DuckDBPyConnection,
    rule: DatasetConfigRule,
    handle: RelationHandle,
    logical: Optional[LogicalType],
    run_id: str,
    executed_at: str,
) -> Tuple[QualityCheckResult, Optional[RunErrorEntry]]:
    """Compile + execute one rule into a result; every failure path returns an
    ERROR result paired with a §6.4 error row (the run continues)."""
    task = f"{rule.rule}:{rule.column}"

    try:
        compiled = compile_rule(rule, handle, logical_type=logical)
    except RuleCompileError as exc:
        msg = f"rule not executed: {_reason(exc)}"
        return (
            _error_result(rule, run_id, executed_at, msg),
            RunErrorEntry(code=ErrorCode.RULE_EXEC_FAILED.value,
                          task=task, message=msg),
        )

    try:
        row = con.execute(compiled.sql, compiled.params).fetchone()
    except duckdb.Error as exc:
        msg = f"rule execution failed: {_reason(exc)}"
        return (
            _error_result(rule, run_id, executed_at, msg, threshold=compiled.threshold),
            RunErrorEntry(code=ErrorCode.RULE_EXEC_FAILED.value,
                          task=task, message=msg),
        )

    if row is None:  # pragma: no cover - aggregates always yield one row
        msg = "rule execution returned no row"
        return (
            _error_result(rule, run_id, executed_at, msg, threshold=compiled.threshold),
            RunErrorEntry(code=ErrorCode.RULE_EXEC_FAILED.value,
                          task=task, message=msg),
        )

    violations = int(row[0] or 0)
    total = int(row[1] or 0)
    refs = row[2] or []

    threshold = compiled.threshold
    if compiled.measure == "outlier":
        # fences are data-dependent → surfaced only after execution (or None on
        # an empty/non-computable relation, honestly omitted).
        lo, hi = _num(row[3]), _num(row[4])
        if lo is not None and hi is not None:
            threshold = CheckThreshold(operator=ThresholdOperator.BETWEEN, value=lo, upper=hi)

    observed, failing = _build_observed(
        compiled.measure, violations, total, compiled.max_rate
    )
    status = _decide_status(
        compiled.measure, violations, total, compiled.max_rate,
        _warn_policy(rule, compiled),
    )
    result = QualityCheckResult(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        rule_id=rule.id,
        rule_version=RULE_VERSION,
        dimension=rule.dimension,
        status=status,
        severity=rule.severity,
        column=rule.column,
        observed=observed,
        threshold=threshold,
        failing_record_count=failing,
        evidence=_evidence(total, refs),
        error=None,
        executed_at=executed_at,
    )
    return result, None


def run_quality_checks(
    con: duckdb.DuckDBPyConnection,
    handle: RelationHandle,
    config: DatasetConfig,
    run_id: str,
    discovery: Optional[DiscoveryReport] = None,
    executed_at: Optional[str] = None,
) -> QualityRunResult:
    """Compile + execute every §5.1 rule in ``config.quality_checks`` against
    the bound ``handle`` relation, returning one :class:`QualityCheckResult`
    per rule (config order) plus §6.4 error rows. ``discovery`` supplies the
    declared logical types ``type_valid`` needs; ``executed_at`` overrides the
    timestamp for deterministic tests (default: current UTC)."""
    logical_of: Dict[str, LogicalType] = {}
    if discovery is not None:
        logical_of = {cd.profile.column: cd.logical for cd in discovery.columns}

    stamp = executed_at or _now_iso()
    checks: List[QualityCheckResult] = []
    errors: List[RunErrorEntry] = []
    for rule in config.quality_checks:
        result, err = _run_one(
            con, rule, handle, logical_of.get(rule.column), run_id, stamp
        )
        checks.append(result)
        if err is not None:
            errors.append(err)
    return QualityRunResult(checks=tuple(checks), errors=tuple(errors))


__all__ = ["QualityRunResult", "run_quality_checks", "RULE_VERSION"]
