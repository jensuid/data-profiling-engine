"""Pydantic domain models hand-aligned to the 9 frozen JSON Schemas (M1-T4).

Source of truth is ``schemas/*.schema.json`` (CONTRACTS §1: "implement these as
pydantic models in Python … validated against the canonical JSON Schemas");
this module mirrors them field-for-field — no shape decisions live here.
Conditional rules from the schemas (§1.1, §2, §4.1, §4.3, §9) are enforced as
validators so a constructed model is always schema-valid by construction.

Round-trip fidelity policy (verified empirically in tests/test_models.py):

- Fields whose schema type is a plain (non-null) type are modelled as
  ``Optional`` with ``None`` meaning "key absent": the overridden
  ``model_dump``/``model_dump_json`` omit them, so a dumped instance always
  validates against the canonical schema.
- Fields marked ``NULLABLE_FIELDS`` (schema type explicitly includes ``"null"`` or is
  untyped, e.g. ``QualityCheckResult.column/observed/error``,
  ``DatasetProfile.grain``, ``RuleCondition.value``) keep an *explicitly set*
  ``None`` in the dump as JSON null; merely-defaulted ``None`` is omitted
  (both validate).

Companion tests: ``tests/test_models.py`` (round-trip + enum/required parity).
"""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Enums (closed sets exactly as the schemas define them)
# ---------------------------------------------------------------------------


class MetricMethod(str, Enum):
    """CONTRACTS §1.1 / provenance.schema.json method enum (ADR-015)."""

    EXACT = "exact"
    APPROXIMATE = "approximate"
    SAMPLED = "sampled"
    NOT_COMPUTED = "not_computed"


class ProfileMode(str, Enum):
    """quick|standard|deep (ENGINE §2.1; profiling_run.config + dataset_config)."""

    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class RunStatus(str, Enum):
    """Terminal job states only (CONTRACTS §6.2; profiling_run.status)."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SourceKind(str, Enum):
    """profiling_run.dataset.source_kind enum."""

    FILE = "file"
    TABLE = "table"
    QUERY = "query"


class SemanticType(str, Enum):
    """column_profile.semantic_type business-role enum (CONTRACTS §3.2)."""

    IDENTIFIER = "identifier"
    DIMENSION = "dimension"
    MEASURE = "measure"
    TIME = "time"
    STATUS = "status"
    TEXT = "text"
    OTHER = "other"


class UnitStatus(str, Enum):
    """known|assumed|unknown — column_profile.unit.status + dataset_config units."""

    KNOWN = "known"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


class QualityDimension(str, Enum):
    """completeness|uniqueness|validity|consistency|timeliness|accuracy_flagged
    (CONTRACTS §4.1; shared by rule dimension and finding category)."""

    COMPLETENESS = "completeness"
    UNIQUENESS = "uniqueness"
    VALIDITY = "validity"
    CONSISTENCY = "consistency"
    TIMELINESS = "timeliness"
    ACCURACY_FLAGGED = "accuracy_flagged"


class CheckStatus(str, Enum):
    """PASS/FAIL/WARN from execution; ERROR = could not execute (never conflate)."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    ERROR = "ERROR"


class Severity(str, Enum):
    """CONTRACTS §4.1 severity scale."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    WARNING = "warning"
    INFO = "info"


class ThresholdOperator(str, Enum):
    """quality_check_result.threshold.operator enum (CONTRACTS §4.1)."""

    LT = "<"
    LTE = "<="
    GT = ">"
    GTE = ">="
    EQ = "=="
    NE = "!="
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"
    MATCHES = "matches"
    UNIQUE = "unique"


class FindingConfidence(str, Enum):
    """finding.confidence (ENGINE §6.2)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class HypothesisStatus(str, Enum):
    """finding.hypotheses[].status — MVP only emits `unverified`."""

    UNVERIFIED = "unverified"
    VERIFYING = "verifying"
    SUPPORTED = "supported"
    REFUTED = "refuted"


class WorkflowStatus(str, Enum):
    """finding_workflow.status enum (CONTRACTS §4.5)."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    ACCEPTED_RISK = "accepted_risk"


class ReasonType(str, Enum):
    """recommendation.reason_type — `evidence` requires non-empty evidence_refs."""

    EVIDENCE = "evidence"
    HEURISTIC = "heuristic"
    BUSINESS_CONTEXT_REQUIRED = "business_context_required"


class RecommendationStatus(str, Enum):
    """recommendation.status — MVP emits `proposed`."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    IMPLEMENTED = "implemented"


class RuleType(str, Enum):
    """dataset_config $defs/rule.rule — closed ENGINE §5.1 MVP catalog."""

    NOT_NULL = "not_null"
    UNIQUE = "unique"
    MISSING_RATE_MAX = "missing_rate_max"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    RANGE = "range"
    VALID_DOMAIN = "valid_domain"
    TYPE_VALID = "type_valid"
    CONDITIONAL_NOT_NULL = "conditional_not_null"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    DATE_ORDER = "date_order"
    OUTLIER_SCAN = "outlier_scan"


class ConditionOperator(str, Enum):
    """dataset_config rule condition.operator enum."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    NOT_IN = "not_in"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"


class BridgeResponseStatus(str, Enum):
    """bridge_response.status discriminator (CONTRACTS §5.3/§5.4)."""

    OK = "ok"
    ACCEPTED = "accepted"
    ERROR = "error"


class JobState(str, Enum):
    """CONTRACTS §6.2 job states; SUCCEEDED/FAILED/CANCELLED terminal.

    The M0-T3 spike runtime keeps its own enum-based model (lifecycle code
    relies on member identity + ``is_terminal``); this canonical mirror is
    the frozen wire shape (bridge_response schema parity + M5-T2 target).
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_HASH_RE = re.compile(r"^(sha256|sha1|md5):[0-9a-fA-F]+$")
_SCHEMA_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+$")
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ContractModel(BaseModel):
    """Base for every contract block, mirroring its canonical JSON Schema.

    ``model_config.extra`` must match the schema's ``additionalProperties``
    (``forbid`` where the schema is strict, ``allow`` where it is open — the
    parity tests assert this per model). The dump overrides implement the
    null/absence policy described in the module docstring so that
    ``model_dump()`` output is always schema-valid JSON.

    Class markers (set on each model, inherited semantics handled here):
    - ``NULLABLE_FIELDS``: field names whose canonical schema type permits null or
      is untyped (``{}``) — an explicitly-set ``None`` is emitted as null.
    - ``NULLABLE_DICT_FIELDS``: field names of untyped dict values (free-form maps
      like ``source_metadata``) — passed through verbatim.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset()
    NULLABLE_DICT_FIELDS: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null(cls, data: Any) -> Any:
        # A canonical schema that types a field without "null" also rejects an
        # explicit JSON null (type check), not just a wrong value. Pydantic
        # would happily accept None for ``Optional[...]``, so enforce parity.
        # Only declared fields are checked: in open models, extra keys follow
        # ``additionalProperties`` and may legitimately be null.
        if isinstance(data, dict):
            fields = cls.model_fields
            for key, value in data.items():
                if (
                    value is None
                    and key in fields
                    and key not in cls.NULLABLE_FIELDS
                ):
                    raise ValueError(
                        f"field {key!r} does not permit null in the canonical "
                        "schema; omit the key instead"
                    )
        return data

    def _dump(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name in type(self).model_fields:
            if name not in self.model_fields_set and getattr(self, name) is None:
                continue  # untouched None == "key absent" policy
            value = getattr(self, name)
            if value is None:
                if name in self.NULLABLE_FIELDS:
                    out[name] = None
                continue
            out[name] = self._dump_value(name, value)
        extra = getattr(self, "__pydantic_extra__", None) or {}
        out.update(extra)
        return out

    def _dump_value(self, field: str, value: Any) -> Any:
        if field in self.NULLABLE_DICT_FIELDS and isinstance(value, dict):
            return value
        if isinstance(value, ContractModel):
            return value._dump()
        if isinstance(value, BaseModel):  # defensive: no plain BaseModels here
            return value.model_dump()
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, list):
            return [self._dump_item(item) for item in value]
        if isinstance(value, dict):
            return {k: (v._dump() if isinstance(v, ContractModel) else v) for k, v in value.items()}
        return value

    @staticmethod
    def _dump_item(item: Any) -> Any:
        if isinstance(item, ContractModel):
            return item._dump()
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, list):
            return [ContractModel._dump_item(i) for i in item]
        if isinstance(item, dict):
            return {k: ContractModel._dump_item(v) for k, v in item.items()}
        return item

    def model_dump(self, **kwargs: Any) -> Dict[str, Any]:
        kwargs.pop("exclude_none", None)
        data = self._dump()
        if kwargs.get("mode") == "json":
            return json.loads(json.dumps(data))
        return data

    def model_dump_json(self, **kwargs: Any) -> str:
        return json.dumps(self._dump())


class _StrictModel(ContractModel):
    """Base for blocks whose schema sets ``additionalProperties: false``."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class _OpenModel(ContractModel):
    """Base for blocks whose schema sets ``additionalProperties: true``."""

    model_config = ConfigDict(extra="allow", use_enum_values=True)


# ---------------------------------------------------------------------------
# provenance.schema.json (§1.1) + $defs/metricValue + $defs/schemaVersion
# ---------------------------------------------------------------------------


class Provenance(_StrictModel):
    """CONTRACTS §1.1 — every computed numeric carries method + method_params."""

    method: MetricMethod
    method_params: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    computed_at: Optional[str] = None
    engine_version: Optional[str] = None

    @field_validator("reason")
    @classmethod
    def _reason_non_empty(cls, v: Optional[str]) -> Optional[str]:
        # schema: {"type": "string", "minLength": 1} — null is not permitted,
        # absence is; None here means "key absent".
        if v is not None and v == "":
            raise ValueError("reason must be non-empty when present")
        return v

    @model_validator(mode="after")
    def _method_rules(self) -> "Provenance":
        m = self.method
        if m == MetricMethod.SAMPLED:
            if not self.method_params or "seed" not in self.method_params:
                raise ValueError("method=sampled requires method_params.seed")
        elif m == MetricMethod.APPROXIMATE:
            if not self.method_params or "algorithm" not in self.method_params:
                raise ValueError("method=approximate requires method_params.algorithm")
        elif m == MetricMethod.NOT_COMPUTED:
            if not self.reason:
                raise ValueError("method=not_computed requires reason")
        return self


class MetricValue(_StrictModel):
    """provenance.schema.json#/$defs/metricValue — value + provenance pairing.

    ``value`` may legitimately be ``null`` per the schema's type list, so an
    explicit JSON null round-trips; "absent" and "null value" both dump as
    ``"value": null`` and validate (schema type includes null).
    """

    value: Optional[Any] = None
    provenance: Provenance
    denominator: Optional[int] = Field(default=None, ge=0)

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"value"})

    @model_validator(mode="after")
    def _not_computed_forbids_value(self) -> "MetricValue":
        # schema allOf: provenance.method == not_computed => `value` key absent.
        # An explicit null value is schema-valid (type includes null) but the
        # contract intent (§1 "never a silent null") is absence, so reject too
        # when the source document carried the key.
        if self.provenance.method == MetricMethod.NOT_COMPUTED and "value" in self.model_fields_set:
            raise ValueError("MetricValue must not carry `value` when method=not_computed")
        return self


# ---------------------------------------------------------------------------
# profiling_run.schema.json (§2)
# ---------------------------------------------------------------------------


class SourceSnapshot(_OpenModel):
    """profiling_run.dataset.source_snapshot (content_hash required)."""

    path: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)
    modified_at: Optional[str] = None
    content_hash: str

    @field_validator("content_hash")
    @classmethod
    def _hash_prefixed(cls, v: str) -> str:
        if not _HASH_RE.match(v):
            raise ValueError("content_hash must be algorithm-prefixed hex (sha256|sha1|md5)")
        return v


class RunDataset(_StrictModel):
    dataset_id: str = Field(min_length=1)
    source_kind: SourceKind
    connector: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_snapshot: SourceSnapshot


class RunConfig(_OpenModel):
    config_version: str
    config_hash: str
    profile_mode: ProfileMode

    @field_validator("config_hash")
    @classmethod
    def _hash_prefixed(cls, v: str) -> str:
        if not _HASH_RE.match(v):
            raise ValueError("config_hash must be algorithm-prefixed hex")
        return v


class RunEngine(_OpenModel):
    engine_version: str
    duckdb_version: Optional[str] = None
    python_version: Optional[str] = None


class RunTiming(_StrictModel):
    started_at: str
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)


class RunSampling(_OpenModel):
    enabled: bool
    strategy: Optional[str] = None
    sample_size: Optional[int] = Field(default=None, ge=0)
    seed: Optional[int] = None

    @model_validator(mode="after")
    def _enabled_requires_all(self) -> "RunSampling":
        if self.enabled and not (self.strategy and self.sample_size is not None and self.seed is not None):
            raise ValueError("sampling.enabled=true requires strategy, sample_size and seed")
        return self


class RunErrorEntry(_OpenModel):
    """profiling_run.error_log[] item — per-task errors preserved without abort (CONTRACTS §6.4)."""

    code: str = Field(min_length=1)
    task: Optional[str] = None
    message: Optional[str] = None


class ProfilingRun(_StrictModel):
    """CONTRACTS §2 — one record per execution; terminal status only (§6.2)."""

    schema_version: str
    run_id: str = Field(min_length=1)
    job_id: Optional[str] = None
    dataset: RunDataset
    config: RunConfig
    engine: RunEngine
    timing: RunTiming
    sampling: Optional[RunSampling] = None
    status: RunStatus
    result_refs: Optional[Dict[str, str]] = None
    error_log: Optional[List[RunErrorEntry]] = None
    warnings: Optional[List[str]] = None

    @field_validator("schema_version")
    @classmethod
    def _semver_lite(cls, v: str) -> str:
        if not _SCHEMA_VERSION_RE.match(v):
            raise ValueError("schema_version must be MAJOR.MINOR")
        return v

    @model_validator(mode="after")
    def _status_terminal(self) -> "ProfilingRun":
        # schema enum is already terminal-only; the Literal run keeps the
        # parity test honest that no intermediate state can ever land here.
        if self.status not in ("SUCCEEDED", "FAILED", "CANCELLED"):
            raise ValueError("status must be a terminal job state (§6.2)")
        return self


# ---------------------------------------------------------------------------
# column_profile.schema.json (§3.2)
# ---------------------------------------------------------------------------


class ColumnUnit(_OpenModel):
    currency: Optional[str] = None
    status: UnitStatus


class Missingness(_StrictModel):
    null_count: int = Field(ge=0)
    blank_count: int = Field(ge=0)
    sentinel_count: int = Field(ge=0)
    missing_rate: MetricValue


class Uniqueness(_StrictModel):
    distinct_count: MetricValue
    is_unique_candidate: bool


class NumericStats(_StrictModel):
    valid_count: Optional[int] = Field(default=None, ge=0)
    min: Optional[MetricValue] = None
    max: Optional[MetricValue] = None
    mean: Optional[MetricValue] = None
    median: Optional[MetricValue] = None
    p25: Optional[MetricValue] = None
    p75: Optional[MetricValue] = None
    p95: Optional[MetricValue] = None
    p99: Optional[MetricValue] = None
    stddev: Optional[MetricValue] = None


class Histogram(_StrictModel):
    bins: List[Union[float, str]]
    counts: List[int]
    provenance: Provenance


class OutlierCandidates(_StrictModel):
    count: int = Field(ge=0)
    method: str
    provenance: Provenance
    sample_row_refs: Optional[List[str]] = None


class Distribution(_StrictModel):
    histogram: Optional[Histogram] = None
    skewness: Optional[MetricValue] = None
    outlier_candidates: Optional[OutlierCandidates] = None


class Categorical(_OpenModel):
    categories: Optional[List[str]] = None
    frequencies: Optional[List[int]] = None
    rare_categories: Optional[List[str]] = None
    label_inconsistencies: Optional[List[Any]] = None


class TemporalRange(_StrictModel):
    min: Optional[str] = None
    max: Optional[str] = None


class Temporal(_OpenModel):
    range: Optional[TemporalRange] = None
    volume_per_granularity: Optional[Dict[str, Any]] = None
    gaps: Optional[Union[Dict[str, Any], List[Any]]] = None
    out_of_expected_range: Optional[Dict[str, Any]] = None
    timezone_source: Optional[str] = None

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"range", "volume_per_granularity", "gaps", "out_of_expected_range", "timezone_source"}
    )
    NULLABLE_DICT_FIELDS: ClassVar[frozenset[str]] = frozenset({"volume_per_granularity", "out_of_expected_range"})


class ColumnProfile(_StrictModel):
    """CONTRACTS §3.2 — physical/logical/semantic split is mandatory (MVP)."""

    schema_version: str
    column: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    physical_type: str = Field(min_length=1)
    logical_type: str = Field(min_length=1)
    semantic_type: SemanticType
    unit: Optional[ColumnUnit] = None
    missingness: Optional[Missingness] = None
    uniqueness: Optional[Uniqueness] = None
    stats: Optional[NumericStats] = None
    distribution: Optional[Distribution] = None
    categorical: Optional[Categorical] = None
    temporal: Optional[Temporal] = None
    visualization_refs: Optional[List[str]] = None
    related_metric_refs: Optional[List[str]] = None

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"unit", "missingness", "uniqueness", "stats", "distribution", "categorical", "temporal"})

    @field_validator("schema_version")
    @classmethod
    def _semver_lite(cls, v: str) -> str:
        if not _SCHEMA_VERSION_RE.match(v):
            raise ValueError("schema_version must be MAJOR.MINOR")
        return v


# ---------------------------------------------------------------------------
# dataset_profile.schema.json (§3.1)
# ---------------------------------------------------------------------------


class TemporalCoverage(_StrictModel):
    column: str = Field(min_length=1)
    # schema type is ["string","null"] — None is a valid, dumpable value.
    min: Optional[str] = None
    max: Optional[str] = None
    provenance: Provenance


class DatasetProfile(_StrictModel):
    """CONTRACTS §3.1 — dataset-level rollup; columns in ordinal order."""

    schema_version: str
    run_id: str = Field(min_length=1)
    row_count: Optional[MetricValue] = None
    column_count: Optional[int] = Field(default=None, ge=0)
    grain: Optional[str] = None  # schema type ["string","null"]
    primary_key_candidates: Optional[List[str]] = None
    temporal_coverage: Optional[TemporalCoverage] = None
    source_metadata: Optional[Dict[str, Any]] = None
    columns: List[ColumnProfile]

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"grain", "temporal_coverage", "source_metadata"})
    NULLABLE_DICT_FIELDS: ClassVar[frozenset[str]] = frozenset({"source_metadata"})

    @field_validator("schema_version")
    @classmethod
    def _semver_lite(cls, v: str) -> str:
        if not _SCHEMA_VERSION_RE.match(v):
            raise ValueError("schema_version must be MAJOR.MINOR")
        return v


# ---------------------------------------------------------------------------
# quality_check_result.schema.json (§4.1)
# ---------------------------------------------------------------------------


class CheckThreshold(_StrictModel):
    operator: ThresholdOperator
    value: Optional[Any] = None  # schema-untyped bound; explicit null allowed
    upper: Optional[Any] = None

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"value", "upper"})


class CheckEvidence(_OpenModel):
    denominator: Optional[int] = Field(default=None, ge=0)
    sample_row_refs: Optional[List[str]] = None
    baseline_ref: Optional[str] = None

    # schema: denominator/baseline_ref typed ["...","null"]
    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"denominator", "baseline_ref"})


class CheckError(_OpenModel):
    code: str = Field(min_length=1)
    message: Optional[str] = None


class QualityCheckResult(_StrictModel):
    """CONTRACTS §4.1 — FAIL ≠ ERROR: an un-executable check is ERROR (§2–3 rules)."""

    schema_version: str
    run_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    dimension: QualityDimension
    status: CheckStatus
    severity: Severity
    column: Optional[str] = None  # null = dataset-scoped rule
    observed: Optional[MetricValue] = None
    threshold: Optional[CheckThreshold] = None
    failing_record_count: Optional[int] = Field(default=None, ge=0)
    evidence: Optional[CheckEvidence] = None
    error: Optional[CheckError] = None
    executed_at: str

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"column", "observed", "threshold", "failing_record_count", "evidence", "error"})

    @field_validator("schema_version")
    @classmethod
    def _semver_lite(cls, v: str) -> str:
        if not _SCHEMA_VERSION_RE.match(v):
            raise ValueError("schema_version must be MAJOR.MINOR")
        return v

    @model_validator(mode="after")
    def _error_coupling(self) -> "QualityCheckResult":
        # schema allOf: status==ERROR <=> error object present and non-null.
        if self.status == CheckStatus.ERROR and self.error is None:
            raise ValueError("status=ERROR requires a populated error object")
        if self.status in (CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.WARN) and self.error is not None:
            raise ValueError(f"status={self.status} must not carry a populated error")
        return self


# ---------------------------------------------------------------------------
# finding_workflow.schema.json (§4.5)
# ---------------------------------------------------------------------------


class WorkflowOwnerRef(_OpenModel):
    id: Optional[str] = None
    display: Optional[str] = None


class WorkflowComment(_OpenModel):
    author: str
    body: str
    created_at: str


class FindingWorkflow(_StrictModel):
    """CONTRACTS §4.5 — Final App shape; MVP emits workflow=null."""

    owner: Optional[Union[WorkflowOwnerRef, str]] = None
    status: WorkflowStatus
    comments: Optional[List[WorkflowComment]] = None
    resolution_evidence: Optional[Any] = None
    updated_at: Optional[str] = None

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"owner", "resolution_evidence", "updated_at"})


# ---------------------------------------------------------------------------
# finding.schema.json (§4.2 / §4.4)
# ---------------------------------------------------------------------------


class FindingScope(_OpenModel):
    column: Optional[str] = None
    affected_records: Optional[int] = Field(default=None, ge=0)
    period: Optional[Union[Dict[str, Any], str]] = None

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"column", "affected_records", "period"})


class FindingFact(_StrictModel):
    """Engine-computed value with provenance only — CONTRACTS §7 forbids
    unverified causes inside `facts`."""

    metric_ref: str = Field(min_length=1)
    value: Optional[Any] = None  # schema-untyped; explicit null permitted
    provenance: Provenance

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"value"})


class FindingHypothesis(_StrictModel):
    text: str = Field(min_length=1)
    status: HypothesisStatus
    suggested_probe: Optional[str] = None

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"suggested_probe"})


class Finding(_StrictModel):
    """CONTRACTS §4.2 — facts / interpretation / hypotheses structurally
    separated; deterministic fingerprint (ADR-016) gives cross-run identity."""

    schema_version: str
    finding_id: str = Field(min_length=1)
    fingerprint: str
    run_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    category: QualityDimension
    severity: Severity
    confidence: FindingConfidence
    scope: FindingScope
    facts: List[FindingFact] = Field(min_length=1)
    # required key in §4.2, but typed ["string","null"] — null is a valid value
    interpretation: Optional[str]
    hypotheses: List[FindingHypothesis]
    evidence_refs: List[str] = Field(min_length=1)
    related_finding_ids: Optional[List[str]] = None
    recommendation_ids: Optional[List[str]] = None
    workflow: Optional[FindingWorkflow] = None

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"interpretation", "workflow"})

    @field_validator("schema_version")
    @classmethod
    def _semver_lite(cls, v: str) -> str:
        if not _SCHEMA_VERSION_RE.match(v):
            raise ValueError("schema_version must be MAJOR.MINOR")
        return v

    @field_validator("fingerprint")
    @classmethod
    def _fingerprint_shape(cls, v: str) -> str:
        if not _FINGERPRINT_RE.match(v):
            raise ValueError("fingerprint must match ^sha256:[0-9a-f]{64}$ (§4.4)")
        return v

    @field_validator("evidence_refs")
    @classmethod
    def _refs_non_empty(cls, v: List[str]) -> List[str]:
        if any(not item for item in v):
            raise ValueError("evidence_refs entries must be non-empty")
        return v

    @field_validator("facts")
    @classmethod
    def _facts_non_empty(cls, v: List[FindingFact]) -> List[FindingFact]:
        if not v:
            raise ValueError("facts requires at least one entry (minItems=1)")
        return v


# ---------------------------------------------------------------------------
# recommendation.schema.json (§4.3)
# ---------------------------------------------------------------------------


class Recommendation(_StrictModel):
    """CONTRACTS §4.3 — must cite existing evidence, never invent causes."""

    schema_version: str
    recommendation_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    reason_type: ReasonType
    target_improvement: Optional[str] = None
    do_not: Optional[List[str]] = None
    evidence_refs: List[str]
    status: RecommendationStatus

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"target_improvement"})

    @field_validator("schema_version")
    @classmethod
    def _semver_lite(cls, v: str) -> str:
        if not _SCHEMA_VERSION_RE.match(v):
            raise ValueError("schema_version must be MAJOR.MINOR")
        return v

    @model_validator(mode="after")
    def _evidence_requires_refs(self) -> "Recommendation":
        if self.reason_type == ReasonType.EVIDENCE and not self.evidence_refs:
            raise ValueError("reason_type=evidence requires non-empty evidence_refs")
        return self


# ---------------------------------------------------------------------------
# dataset_config.schema.json (§9)
# ---------------------------------------------------------------------------


class ConfigUnitAssertion(_OpenModel):
    currency: Optional[str] = None
    status: UnitStatus


class ConfigDataset(_StrictModel):
    name: str = Field(min_length=1)
    grain: Optional[str] = Field(default=None, min_length=1)
    primary_key: Optional[List[str]] = None
    units: Optional[Dict[str, ConfigUnitAssertion]] = None

    @field_validator("primary_key")
    @classmethod
    def _pk_shape(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        if len(v) < 1:
            raise ValueError("primary_key must be non-empty (minItems=1)")
        if len(set(v)) != len(v):
            raise ValueError("primary_key entries must be unique")
        if any(not item for item in v):
            raise ValueError("primary_key entries must be non-empty")
        return v


class ConfigSampling(_OpenModel):
    enabled: bool
    strategy: Optional[str] = None
    # NOTE (§9 gotcha): config sampling uses `size`, ProfilingRun uses `sample_size`.
    size: Optional[int] = Field(default=None, ge=1)
    seed: Optional[int] = None

    @model_validator(mode="after")
    def _enabled_requires_all(self) -> "ConfigSampling":
        if self.enabled and not (self.strategy and self.size is not None and self.seed is not None):
            raise ValueError("sampling.enabled=true requires strategy, size and seed")
        return self


class ConfigMissing(_StrictModel):
    """ADR-017 — missingness profiling knobs. Sentinel placeholders are
    per-dataset business knowledge, declared here, never hard-coded in the
    engine (AGENTS §5). Exact, case-sensitive match on the raw stringified
    value; NULL never matches; absent/empty ⇒ sentinel_count = 0."""

    sentinel_values: Optional[List[str]] = None

    @field_validator("sentinel_values")
    @classmethod
    def _sentinel_shape(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        if len(set(v)) != len(v):
            raise ValueError("sentinel_values entries must be unique")
        if any(not item for item in v):
            raise ValueError("sentinel_values entries must be non-empty")
        return v


class ConfigProfiling(_StrictModel):
    mode: ProfileMode
    sampling: Optional[ConfigSampling] = None
    sections: Optional[List[str]] = None
    missing: Optional[ConfigMissing] = None

    @field_validator("sections")
    @classmethod
    def _sections_unique(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None and len(set(v)) != len(v):
            raise ValueError("sections entries must be unique")
        if v is not None and any(not item for item in v):
            raise ValueError("sections entries must be non-empty")
        return v


class RuleCondition(_StrictModel):
    column: str = Field(min_length=1)
    operator: ConditionOperator
    value: Optional[Any] = None  # schema requires the key; value may be null

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"value"})


class DatasetConfigRule(_StrictModel):
    """CONTRACTS §9 rule entry; ENGINE §5.1 closed MVP catalog."""

    id: str = Field(min_length=1)
    rule: RuleType
    column: str = Field(min_length=1)
    dimension: QualityDimension
    severity: Severity
    value: Optional[Any] = None
    threshold: Optional[Any] = None
    condition: Optional[RuleCondition] = None
    business_note: Optional[str] = None

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"value", "threshold", "condition"})

    @model_validator(mode="after")
    def _rule_conditionals(self) -> "DatasetConfigRule":
        r = self.rule
        has_param = self.value is not None or self.threshold is not None or "value" in self.model_fields_set or "threshold" in self.model_fields_set
        if r in (
            RuleType.MISSING_RATE_MAX,
            RuleType.GREATER_THAN_OR_EQUAL,
            RuleType.LESS_THAN_OR_EQUAL,
            RuleType.RANGE,
            RuleType.VALID_DOMAIN,
        ) and not has_param:
            raise ValueError(f"rule={r} requires `value` or `threshold`")
        if r == RuleType.CONDITIONAL_NOT_NULL and self.condition is None:
            raise ValueError("rule=conditional_not_null requires `condition`")
        return self


class DatasetConfig(_StrictModel):
    """CONTRACTS §9 — input contract validated BEFORE any profiling starts.

    ``config_version`` is an integer here (gotcha: unlike the string
    ``schema_version`` on persisted objects).
    """

    config_version: int = Field(ge=1)
    dataset: ConfigDataset
    profiling: ConfigProfiling
    quality_checks: List[DatasetConfigRule] = Field(min_length=1)

    @field_validator("quality_checks")
    @classmethod
    def _at_least_one_rule(
        cls, v: List[DatasetConfigRule]
    ) -> List[DatasetConfigRule]:
        if not v:
            raise ValueError("quality_checks requires at least one rule (minItems=1)")
        return v


# ---------------------------------------------------------------------------
# bridge / job envelopes (CONTRACTS §5.3, §6.2) — M1-T6
# ---------------------------------------------------------------------------

_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class BridgeRequest(_StrictModel):
    """CONTRACTS §5.3 request envelope. One JS→Python call; ``command`` is
    resolved against the §5.2 allow-list by the facade, ``payload`` against the
    command-specific schema. Extra top-level fields forbidden (SECURITY §3)."""

    command: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)
    payload: Dict[str, Any] = Field(default_factory=dict)


class BridgeErrorObject(_StrictModel):
    """CONTRACTS §5.3 error detail; never stack traces or raw sensitive values
    (SECURITY §5). ``code`` is a §8 registry code (open, extensible)."""

    code: str
    message: str = Field(min_length=1, max_length=512)
    retryable: bool = False

    @field_validator("code")
    @classmethod
    def _code_shape(cls, v: str) -> str:
        if not _ERROR_CODE_RE.match(v):
            raise ValueError("error code must be an uppercase §8 identifier")
        return v


class BridgeResponse(_StrictModel):
    """CONTRACTS §5.3 response envelope. ``status`` discriminates exactly one
    body slot: ``ok``→``result``, ``accepted``→``job_id``, ``error``→``error``
    (§5.4). Unused slots are absent, never null."""

    request_id: str = Field(max_length=128)
    status: BridgeResponseStatus
    result: Optional[Dict[str, Any]] = None
    job_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    error: Optional[BridgeErrorObject] = None

    @model_validator(mode="after")
    def _discriminate_body(self) -> "BridgeResponse":
        has_result = "result" in self.model_fields_set and self.result is not None
        has_job = "job_id" in self.model_fields_set and self.job_id is not None
        has_error = "error" in self.model_fields_set and self.error is not None
        if self.status == BridgeResponseStatus.OK:
            if not has_result:
                raise ValueError("status=ok requires a populated result")
            if has_job or has_error:
                raise ValueError("status=ok must not carry job_id or error")
        elif self.status == BridgeResponseStatus.ACCEPTED:
            if not has_job:
                raise ValueError("status=accepted requires job_id")
            if has_result or has_error:
                raise ValueError("status=accepted must not carry result or error")
        else:  # ERROR
            if not has_error:
                raise ValueError("status=error requires a populated error")
            if has_result or has_job:
                raise ValueError("status=error must not carry result or job_id")
        return self


class JobProgress(_StrictModel):
    """CONTRACTS §5.3 progress block, embedded in JobStatus."""

    completed_steps: int = Field(default=0, ge=0)
    total_steps: int = Field(default=0, ge=0)
    current_step: Optional[str] = None


class JobStatus(_StrictModel):
    """CONTRACTS §5.3 polled-status object (§6.2 states). ``error_code`` carries
    a §8 registry code on failure; ``result`` is the bounded job output on
    success. The M0-T3 spike runtime model is separate (enum identity for the
    lifecycle); this is the frozen wire shape validated against the schema."""

    job_id: str = Field(min_length=1, max_length=128)
    status: JobState
    progress: JobProgress
    error_code: Optional[str] = Field(default=None, min_length=1)
    result: Optional[Dict[str, Any]] = None

    @field_validator("error_code")
    @classmethod
    def _code_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _ERROR_CODE_RE.match(v):
            raise ValueError("error_code must be an uppercase §8 identifier")
        return v


__all__ = [
    # enums
    "MetricMethod",
    "ProfileMode",
    "RunStatus",
    "SourceKind",
    "SemanticType",
    "UnitStatus",
    "QualityDimension",
    "CheckStatus",
    "Severity",
    "ThresholdOperator",
    "FindingConfidence",
    "HypothesisStatus",
    "WorkflowStatus",
    "ReasonType",
    "RecommendationStatus",
    "RuleType",
    "ConditionOperator",
    "BridgeResponseStatus",
    "JobState",
    # provenance
    "Provenance",
    "MetricValue",
    # run
    "SourceSnapshot",
    "RunDataset",
    "RunConfig",
    "RunEngine",
    "RunTiming",
    "RunSampling",
    "RunErrorEntry",
    "ProfilingRun",
    # profiles
    "ColumnUnit",
    "Missingness",
    "Uniqueness",
    "NumericStats",
    "Histogram",
    "OutlierCandidates",
    "Distribution",
    "Categorical",
    "TemporalRange",
    "Temporal",
    "ColumnProfile",
    "TemporalCoverage",
    "DatasetProfile",
    # quality
    "CheckThreshold",
    "CheckEvidence",
    "CheckError",
    "QualityCheckResult",
    # findings
    "WorkflowOwnerRef",
    "WorkflowComment",
    "FindingWorkflow",
    "FindingScope",
    "FindingFact",
    "FindingHypothesis",
    "Finding",
    "Recommendation",
    # config
    "ConfigUnitAssertion",
    "ConfigDataset",
    "ConfigSampling",
    "ConfigMissing",
    "ConfigProfiling",
    "RuleCondition",
    "DatasetConfigRule",
    "DatasetConfig",
    # bridge / job envelopes (M1-T6)
    "BridgeRequest",
    "BridgeErrorObject",
    "BridgeResponse",
    "JobProgress",
    "JobStatus",
]
