"""Discovery result types (M2-T2, ENGINE §4.1).

Physical/logical/semantic triple, unit status and confidence are carried on the
contract surface (:class:`ColumnProfile`); confidence + rationale + the bounded
signals that justified the call live here, in engine-internal objects. Downstream
modules (metric planner M2-T3, findings M2-T8) consume this report before it is
folded into the persisted :class:`DatasetProfile`.

The logical-type vocabulary is engine-side; ``column_profile.logical_type``
(CONTRACTS §3.2) is intentionally an open string so Phase-2 sources can add
vocabularies without a schema change, but the closed enum below is the *only*
set the MVP inference rules are permitted to emit (AGENTS §5 — metadata-driven,
predictable planner inputs).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from data_profiling_engine.schemas.models import ColumnProfile


class LogicalType(str, Enum):
    """Closed MVP vocabulary for ``ColumnProfile.logical_type``."""

    UNKNOWN = "unknown"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    NUMERIC_CURRENCY = "numeric_currency"
    STRING = "string"
    TEXT_FREEFORM = "text_freeform"
    CATEGORICAL_NOMINAL = "categorical_nominal"
    EMAIL = "email"
    URL = "url"
    PHONE = "phone"
    UUID = "uuid"
    TEMPORAL_DATE = "temporal_date"
    TEMPORAL_TIME = "temporal_time"
    TEMPORAL_DATETIME = "temporal_datetime"
    BINARY = "binary"
    NESTED = "nested"


class Confidence(str, Enum):
    """Inference confidence, so misclassification is visible (ENGINE §4.1)."""

    HIGH = "high"      # config-declared, unambiguous physical family, or ≥90% pattern hit
    MEDIUM = "medium"  # pattern/name heuristic with 70-90% match, or coerced temporal
    LOW = "low"        # fallback (unknown / string default)


@dataclass(frozen=True)
class ColumnSignals:
    """Bounded aggregates collected from the DuckDB view (never full rows;
    ADR-007). Only what the inference rules need — heavy profiling metrics
    land later in M2-T4..T6 with their own Provenance."""

    n_rows: int
    n_null: int
    n_notnull: int
    n_distinct: int
    distinct_method: str = "exact"  # exact | approximate (HLL); internal, not a contract metric

    # VARCHAR-only:
    min_len: Optional[int] = None
    max_len: Optional[int] = None
    n_blank: Optional[int] = None
    sample: Tuple[str, ...] = ()

    # Numeric / temporal-only (repr strings so they serialise without JSON codecs):
    min_repr: Optional[str] = None
    max_repr: Optional[str] = None

    # BOOLEAN-only:
    n_true: Optional[int] = None
    n_false: Optional[int] = None

    @property
    def cardinality_ratio(self) -> float:
        """``n_distinct / n_notnull`` (0 when the column is fully null). Drives
        the identifier↔categorical↔measure split (ENGINE §4.1)."""
        if self.n_notnull <= 0:
            return 0.0
        return self.n_distinct / self.n_notnull


@dataclass(frozen=True)
class ColumnDiscovery:
    """One column's discovery outcome: the contract-valid profile skeleton
    (type triple + unit only — sub-blocks are filled by later profilers),
    plus engine-internal rationale."""

    profile: ColumnProfile
    logical: LogicalType
    confidence: Confidence
    rationale: str
    signals: ColumnSignals
    config_declared: bool = False  # semantic/unit forced by dataset_config (config wins)


@dataclass(frozen=True)
class DiscoveryReport:
    """Result of one discovery pass over a bound relation."""

    dataset_id: str
    columns: List[ColumnDiscovery] = field(default_factory=list)

    def profiles(self) -> List[ColumnProfile]:
        return [c.profile for c in self.columns]


__all__ = [
    "LogicalType",
    "Confidence",
    "ColumnSignals",
    "ColumnDiscovery",
    "DiscoveryReport",
]
