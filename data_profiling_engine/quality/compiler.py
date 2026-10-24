"""Rule compiler — ENGINE §5.2 "each rule compiles to ONE SQL statement
returning ``(violations, total, sample_row_ids)`` against a relation".

M2-T7 delivers the *quality side* of the engine (the first task that is not a
``ColumnProfile`` metric family): consume a frozen :class:`DatasetConfigRule`
(CONTRACTS §9) and emit one DuckDB statement, executed by
:mod:`data_profiling_engine.quality.executor`, that yields the per-rule
verdict inputs. Discipline mirrored from the M2-T3 ``render_sql`` single
materialisation point (AGENTS §3, ADR-017):

- **No user value is ever interpolated into SQL text.** Bounds / domains /
  condition matchers arrive as ``$n`` **bound parameters**; identifiers (view,
  column) are the only things quoted inline, and every column is validated to
  exist on the bound relation first (a missing column is a compile error →
  ``ERROR`` at execution, never a silently-passing check).
- **Memory-flat** (ADR-007): the verdict is a one-row scalar aggregate; the
  evidence ``sample_row_ids`` are drawn from a bounded (``LIMIT``) sub-query so
  a 100M-row scan never materialises a large array (≤ ``_SAMPLE_REF_CAP``).
- **Deterministic** evidence: refs are ordered by the connector row-id so the
  same file + rule yields the same ``row:N`` refs (ENGINE §5.2 reproducibility).

The compiled statement always returns five columns —
``(violations, total, sample_row_ids, lower_bound, upper_bound)`` — the last
two are populated only by ``outlier_scan`` (the Tukey fences), keeping one
uniform fetch path in the executor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery.types import LogicalType
from data_profiling_engine.engine.planner import _q as _quote_ident
from data_profiling_engine.schemas.models import (
    CheckThreshold,
    DatasetConfigRule,
    RuleCondition,
    RuleType,
    ThresholdOperator,
)
from data_profiling_engine.quality import registry

#: ENGINE §5.2 — ``sample_row_ids`` capped (≤200) to respect the CONTRACTS §5.4
#: bridge payload limit; full lists go to artifacts via export (out of M2-T7).
_SAMPLE_REF_CAP = 200

#: Logical type → a DuckDB scalar type for ``type_valid`` TRY_CAST enforcement.
#: String-ish logicals (string / text / email / url / phone / uuid / categorical
#: / unknown) have no stricter MVP parse to check — the physical column already
#: read as text — so they compile to an always-pass (0-violation) predicate.
_CAST_FOR_LOGICAL: Dict[LogicalType, str] = {
    LogicalType.BOOLEAN: "BOOLEAN",
    LogicalType.INTEGER: "BIGINT",
    LogicalType.FLOAT: "DOUBLE",
    LogicalType.DECIMAL: "DECIMAL(18,6)",
    LogicalType.NUMERIC_CURRENCY: "DOUBLE",
    LogicalType.TEMPORAL_DATE: "DATE",
    LogicalType.TEMPORAL_TIME: "TIME",
    LogicalType.TEMPORAL_DATETIME: "TIMESTAMP",
}

#: Physical DuckDB type → logical, as a fallback when a caller runs rules
#: without a discovery report (a bare CLI / unit harness). Mirrors the
#: connectors' (name, physical-type) tuple; family prefix-matched.
def _logical_from_physical(physical: str) -> Optional[LogicalType]:
    p = (physical or "").upper()
    if p.startswith(("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT", "UINTEGER")):
        return LogicalType.INTEGER
    if p.startswith(("DOUBLE", "REAL", "FLOAT")):
        return LogicalType.FLOAT
    if p.startswith("DECIMAL"):
        return LogicalType.DECIMAL
    if p.startswith("BOOL"):
        return LogicalType.BOOLEAN
    if p.startswith("TIMESTAMP"):
        return LogicalType.TEMPORAL_DATETIME
    if p == "DATE":
        return LogicalType.TEMPORAL_DATE
    if p == "TIME":
        return LogicalType.TEMPORAL_TIME
    return None


class RuleCompileError(Exception):
    """A rule that cannot be turned into an executable statement (target column
    absent, malformed parameter shape, or an unsupported/unusable type for the
    rule). The executor records this as ``status=ERROR`` per ENGINE §5.3 —
    the engine has NOT proven the rule holds; FAIL≠ERROR (AGENTS §3)."""


@dataclass(frozen=True)
class CompiledRule:
    """One executable statement plus the metadata the executor needs to read a
    verdict off its ``(violations, total, ...)`` row."""

    sql: str
    params: List[Any] = field(default_factory=list)
    measure: str = "count"                       # 'count' | 'rate' | 'outlier'
    threshold: Optional[CheckThreshold] = None
    warn_only: bool = False
    max_rate: Optional[float] = None             # only for measure == 'rate'


# ---------------------------------------------------------------------------
# parameter binding helpers (never interpolate; append + emit $n placeholder)
# ---------------------------------------------------------------------------


class _Binder:
    """Collect bound values in call order and hand back the matching ``$n``
    token, so the SQL text carries only placeholders (ADR-017 discipline)."""

    def __init__(self) -> None:
        self.values: List[Any] = []

    def bind(self, value: Any) -> str:
        self.values.append(value)
        return f"${len(self.values)}"

    def bind_many(self, values: List[Any]) -> str:
        """Comma-joined placeholder list for ``IN (...)`` / domain sets."""
        return ", ".join(self.bind(v) for v in values)


def _ref_subquery(view: str, rid: str, viol: str) -> str:
    """Bounded, deterministic ``sample_row_ids`` scalar sub-expression: at most
    ``_SAMPLE_REF_CAP`` violating row-ids ordered by the connector row id
    (empty ⇒ the outer ``COALESCE`` yields ``[]``)."""
    return (
        f"(SELECT array_agg(rid) FROM (SELECT {rid} AS rid FROM {_quote_ident(view)} "
        f"WHERE ({viol}) ORDER BY rid LIMIT {_SAMPLE_REF_CAP}) s)"
    )


def _generic_statement(
    view: str, rid: str, viol: str, pop: str, binder: _Binder,
    lower: Optional[str] = None, upper: Optional[str] = None,
) -> str:
    """The ENGINE §5.2 canonical one-row aggregate for count / rate rules.
    ``viol`` is the violation predicate, ``pop`` the population predicate
    (``TRUE`` for full-relation rules, the condition for conditional rules)."""
    lo = lower if lower is not None else "NULL::DOUBLE"
    hi = upper if upper is not None else "NULL::DOUBLE"
    return (
        "SELECT "
        f"COUNT(*) FILTER (WHERE ({viol})) AS violations, "
        f"COUNT(*) FILTER (WHERE ({pop})) AS total, "
        f"COALESCE({_ref_subquery(view, rid, viol)}, []) AS sample_row_ids, "
        f"{lo} AS lower_bound, {hi} AS upper_bound "
        f"FROM {_quote_ident(view)}"
    )


# ---------------------------------------------------------------------------
# value extraction from a DatasetConfigRule (value vs threshold slots)
# ---------------------------------------------------------------------------


def _scalar_param(rule: DatasetConfigRule, kind: str) -> Any:
    """Pull the single bound value a bound/rate rule needs. Both the §5.1
    ``value`` slot and the ``threshold`` slot are permitted; ``value`` wins when
    present (mirrors the §9 exemplars, which write the bound under ``value:``)."""
    raw = rule.value if rule.value is not None else rule.threshold
    if raw is None:
        raise RuleCompileError(f"rule {rule.rule!r} requires a numeric {kind} value")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise RuleCompileError(f"rule {rule.rule!r} {kind} must be a number, got {type(raw).__name__}")
    return raw


def _bounds_param(rule: DatasetConfigRule) -> Tuple[Any, Any]:
    """Range bounds: a 2-element ``value``/``threshold`` sequence ``[low, high]``
    (or a ``{lower|min, upper|max}`` mapping). Malformed shape → ERROR."""
    raw = rule.value if rule.value is not None else rule.threshold
    if isinstance(raw, dict):
        low = raw.get("lower", raw.get("min"))
        high = raw.get("upper", raw.get("max"))
        if low is None or high is None:
            raise RuleCompileError("range rule needs lower/upper (or min/max) bounds")
        return low, high
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return raw[0], raw[1]
    raise RuleCompileError("range rule requires a [low, high] pair")


def _domain_param(rule: DatasetConfigRule) -> List[Any]:
    """Allowed set for ``valid_domain``: a non-empty ``value``/``threshold`` list."""
    raw = rule.value if rule.value is not None else rule.threshold
    if isinstance(raw, (list, tuple)) and raw:
        return list(raw)
    raise RuleCompileError("valid_domain rule requires a non-empty allowed-value list")


def _col(rule: DatasetConfigRule, handle: RelationHandle) -> str:
    """Validate + quote the rule's target column against the bound relation."""
    if rule.column not in handle.column_names:
        raise RuleCompileError(f"rule column {rule.column!r} not present in the relation")
    return _quote_ident(rule.column)


# ---------------------------------------------------------------------------
# per-rule compilation
# ---------------------------------------------------------------------------


def _compile_condition(cond: RuleCondition, handle: RelationHandle, binder: _Binder) -> str:
    """Condition matcher predicate (ENGINE §5.2 conditional FILTER); the match
    value is bound, never interpolated. ``in``/``not_in`` take a value list."""
    if cond.column not in handle.column_names:
        raise RuleCompileError(f"condition column {cond.column!r} not present in the relation")
    cc = _quote_ident(cond.column)
    op = cond.operator
    if op in ("in", "not_in"):
        if not isinstance(cond.value, (list, tuple)) or not cond.value:
            raise RuleCompileError(f"condition operator {op!r} requires a non-empty value list")
        ph = binder.bind_many(list(cond.value))
        return f"{cc} {'IN' if op == 'in' else 'NOT IN'} ({ph})"
    sym = {
        "equals": "=", "not_equals": "<>", "greater_than": ">", "less_than": "<",
    }[op]
    return f"{cc} {sym} {binder.bind(cond.value)}"


def _missing_predicate(col: str) -> str:
    """ENGINE §5.2 missing-rate definition, verbatim from the spec example:
    NULL or blank (trimmed-empty) — sentinel placeholders are a ColumnProfile
    metric concern (ADR-017/M2-T4), deliberately NOT folded into the rule here."""
    return f"{col} IS NULL OR trim(CAST({col} AS VARCHAR)) = ''"


def _compile_outliers(view: str, rid: str, col: str) -> str:
    """Tukey IQR/1.5 fences (the M2-T6 outlier method reused at rule scale),
    single statement: fences via one ``quantile_cont`` pass, cross-joined so the
    bounds ride the result row for the ``threshold`` block."""
    lo = "b.q1 - 1.5 * (b.q3 - b.q1)"
    hi = "b.q3 + 1.5 * (b.q3 - b.q1)"
    viol = f"{col} IS NOT NULL AND ({col} < {lo} OR {col} > {hi})"
    return (
        f"WITH b AS (SELECT quantile_cont(CAST({col} AS DOUBLE), 0.25) AS q1, "
        f"quantile_cont(CAST({col} AS DOUBLE), 0.75) AS q3 FROM {_quote_ident(view)}) "
        "SELECT "
        f"(SELECT COUNT(*) FROM {_quote_ident(view)}, b WHERE {viol}) AS violations, "
        f"(SELECT COUNT(*) FROM {_quote_ident(view)} WHERE {col} IS NOT NULL) AS total, "
        f"COALESCE((SELECT array_agg(rid) FROM (SELECT {rid} AS rid FROM {_quote_ident(view)}, b "
        f"WHERE {viol} ORDER BY rid LIMIT {_SAMPLE_REF_CAP}) s), []) AS sample_row_ids, "
        "(SELECT b.q1 - 1.5 * (b.q3 - b.q1) FROM b) AS lower_bound, "
        "(SELECT b.q3 + 1.5 * (b.q3 - b.q1) FROM b) AS upper_bound "
        "FROM b"
    )


def compile_rule(
    rule: DatasetConfigRule,
    handle: RelationHandle,
    logical_type: Optional[LogicalType] = None,
) -> CompiledRule:
    """Compile one §5.1 rule to a :class:`CompiledRule`. Raises
    :class:`RuleCompileError` when the rule cannot execute (absent column, bad
    parameter shape, non-numeric target) — the executor turns that into an
    ``ERROR`` result, never a ``FAIL``."""
    defn = registry.lookup(rule.rule)
    if not defn.mvp_active:
        raise RuleCompileError(
            f"rule type {rule.rule!r} is Phase-2/deep and is not executed in MVP (ENGINE §5.1)"
        )

    view = handle.view_name
    rid = _quote_ident(handle.row_id_column)
    binder = _Binder()

    # -- measure dispatch (defn.rule_type is a real enum member; rule.rule is a
    # plain string under use_enum_values, so compare on the registry value) ----
    rtype = defn.rule_type
    if rtype is RuleType.UNIQUE:
        # No target-column value is bound; validate the column exists.
        col = _col(rule, handle)
        # violations = minimum rows to drop to make the column unique, i.e.
        # non-null rows minus distinct non-null values (a per-group measure,
        # not a per-row window sum which would double-count each duplicate).
        sql = (
            f"WITH g AS (SELECT {rid} AS rid, {col} AS v, "
            f"COUNT(*) OVER (PARTITION BY {col}) AS grp_n "
            f"FROM {_quote_ident(view)} WHERE {col} IS NOT NULL) "
            "SELECT "
            "(SELECT COUNT(*) - COUNT(DISTINCT v) FROM g) AS violations, "
            "(SELECT COUNT(*) FROM g) AS total, "
            f"COALESCE((SELECT array_agg(rid) FROM (SELECT rid FROM g WHERE grp_n > 1 ORDER BY rid LIMIT {_SAMPLE_REF_CAP}) s), []) AS sample_row_ids, "
            "NULL::DOUBLE AS lower_bound, NULL::DOUBLE AS upper_bound"
        )
        return CompiledRule(
            sql=sql, params=[], measure="count", warn_only=defn.warn_only,
            threshold=CheckThreshold(operator=ThresholdOperator.UNIQUE),
        )

    if rtype is RuleType.OUTLIER_SCAN:
        col = _col(rule, handle)
        return CompiledRule(
            sql=_compile_outliers(view, rid, col), params=binder.values,
            measure="outlier", warn_only=True,
        )

    # remaining rules all reference the target column + a bounded aggregate
    col = _col(rule, handle)

    if rtype is RuleType.NOT_NULL:
        viol = f"{col} IS NULL"
        return CompiledRule(
            sql=_generic_statement(view, rid, viol, "TRUE", binder),
            params=binder.values, measure="count", warn_only=defn.warn_only,
            threshold=CheckThreshold(operator=ThresholdOperator.NOT_NULL),
        )

    if rtype is RuleType.MISSING_RATE_MAX:
        bound = float(_scalar_param(rule, "missing-rate"))
        viol = _missing_predicate(col)
        return CompiledRule(
            sql=_generic_statement(view, rid, viol, "TRUE", binder),
            params=binder.values, measure="rate", warn_only=defn.warn_only,
            threshold=CheckThreshold(operator=ThresholdOperator.LTE, value=bound),
            max_rate=bound,
        )

    if rtype is RuleType.GREATER_THAN_OR_EQUAL:
        bound = _scalar_param(rule, "lower")
        ph = binder.bind(bound)
        viol = f"{col} < {ph}"
        return CompiledRule(
            sql=_generic_statement(view, rid, viol, "TRUE", binder),
            params=binder.values, measure="count", warn_only=defn.warn_only,
            threshold=CheckThreshold(operator=ThresholdOperator.GTE, value=bound),
        )

    if rtype is RuleType.LESS_THAN_OR_EQUAL:
        bound = _scalar_param(rule, "upper")
        ph = binder.bind(bound)
        viol = f"{col} > {ph}"
        return CompiledRule(
            sql=_generic_statement(view, rid, viol, "TRUE", binder),
            params=binder.values, measure="count", warn_only=defn.warn_only,
            threshold=CheckThreshold(operator=ThresholdOperator.LTE, value=bound),
        )

    if rtype is RuleType.RANGE:
        low, high = _bounds_param(rule)
        plo, phi = binder.bind(low), binder.bind(high)
        viol = f"{col} IS NOT NULL AND ({col} < {plo} OR {col} > {phi})"
        return CompiledRule(
            sql=_generic_statement(view, rid, viol, "TRUE", binder),
            params=binder.values, measure="count", warn_only=defn.warn_only,
            threshold=CheckThreshold(operator=ThresholdOperator.BETWEEN, value=low, upper=high),
        )

    if rtype is RuleType.VALID_DOMAIN:
        domain = _domain_param(rule)
        ph = binder.bind_many(domain)
        viol = f"{col} IS NOT NULL AND {col} NOT IN ({ph})"
        return CompiledRule(
            sql=_generic_statement(view, rid, viol, "TRUE", binder),
            params=binder.values, measure="count", warn_only=defn.warn_only,
            threshold=CheckThreshold(operator=ThresholdOperator.IN, value=domain),
        )

    if rtype is RuleType.TYPE_VALID:
        lt = logical_type if logical_type is not None else _logical_from_physical(
            dict(handle.columns).get(rule.column, "")
        )
        cast = _CAST_FOR_LOGICAL.get(lt) if lt is not None else None
        if cast is None:
            # No stricter MVP parse for a text-ish / unmapped logical → the
            # column already read as its physical type ⇒ always-pass predicate.
            viol = "FALSE"
        else:
            viol = f"{col} IS NOT NULL AND TRY_CAST({col} AS {cast}) IS NULL"
        return CompiledRule(
            sql=_generic_statement(view, rid, viol, "TRUE", binder),
            params=binder.values, measure="count", warn_only=defn.warn_only,
            threshold=CheckThreshold(
                operator=ThresholdOperator.MATCHES,
                value=(lt.value if lt is not None else "unknown"),
            ),
        )

    if rtype is RuleType.CONDITIONAL_NOT_NULL:
        if rule.condition is None:  # gated by config schema; defensive
            raise RuleCompileError("conditional_not_null rule requires a `condition`")
        pred = _compile_condition(rule.condition, handle, binder)
        viol = f"{pred} AND {col} IS NULL"
        return CompiledRule(
            sql=_generic_statement(view, rid, viol, pred, binder),
            params=binder.values, measure="count", warn_only=defn.warn_only,
            threshold=CheckThreshold(operator=ThresholdOperator.NOT_NULL),
        )

    raise RuleCompileError(f"no compiler for rule type {rtype!r}")  # pragma: no cover


__all__ = [
    "CompiledRule",
    "RuleCompileError",
    "compile_rule",
    "_SAMPLE_REF_CAP",
]
