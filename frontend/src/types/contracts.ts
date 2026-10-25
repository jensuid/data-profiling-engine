/**
 * M1-T5 — TypeScript mirror of the frozen JSON Schemas (CONTRACTS §1; AGENTS §3).
 *
 * CONTRACTS §1 requires every cross-boundary object to be implemented both as a
 * pydantic model in Python and mirrored as a TypeScript type in React, "both
 * generated from / validated against the canonical JSON Schemas in schemas/".
 * The canonical schemas (data_profiling_engine/schemas/*.schema.json) are the
 * source of truth; this file mirrors `schemas/models.py` field-for-field so the
 * front-end and back-end share one contract shape (parity is enforced by
 * tests/test_contracts_ts_parity.py against the schemas, not by hand).
 *
 * Conventions used here (kept machine-parseable for the parity test):
 * - Every closed enum is a `<Name>Values` const tuple + a union `<Name>`.
 * - Optional (non-required) schema fields carry `?`.
 * - Fields whose canonical type includes `null` also carry `| null`.
 * - Open blocks (schema `additionalProperties: true`) carry a `[key: string]:
 *   unknown` index signature; strict blocks do not.
 */

/** Any JSON scalar / bounded structure (schemas use untyped/free-form values). */
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

// ---------------------------------------------------------------------------
// Closed enums (CONTRACTS §1.1, §2, §3.2, §4, §6.2, §9) — exactly the schema sets.
// ---------------------------------------------------------------------------

export const MetricMethodValues = [
  "exact",
  "approximate",
  "sampled",
  "not_computed",
] as const;
export type MetricMethod = (typeof MetricMethodValues)[number];

export const ProfileModeValues = ["quick", "standard", "deep"] as const;
export type ProfileMode = (typeof ProfileModeValues)[number];

export const ReportLanguageValues = ["en", "id"] as const;
export type ReportLanguage = (typeof ReportLanguageValues)[number];

export const RunStatusValues = ["SUCCEEDED", "FAILED", "CANCELLED"] as const;
export type RunStatus = (typeof RunStatusValues)[number];

export const SourceKindValues = ["file", "table", "query"] as const;
export type SourceKind = (typeof SourceKindValues)[number];

export const SemanticTypeValues = [
  "identifier",
  "dimension",
  "measure",
  "time",
  "status",
  "text",
  "other",
] as const;
export type SemanticType = (typeof SemanticTypeValues)[number];

export const UnitStatusValues = ["known", "assumed", "unknown"] as const;
export type UnitStatus = (typeof UnitStatusValues)[number];

export const QualityDimensionValues = [
  "completeness",
  "uniqueness",
  "validity",
  "consistency",
  "timeliness",
  "accuracy_flagged",
] as const;
export type QualityDimension = (typeof QualityDimensionValues)[number];

export const CheckStatusValues = ["PASS", "FAIL", "WARN", "ERROR"] as const;
export type CheckStatus = (typeof CheckStatusValues)[number];

export const SeverityValues = [
  "critical",
  "high",
  "medium",
  "low",
  "warning",
  "info",
] as const;
export type Severity = (typeof SeverityValues)[number];

export const ThresholdOperatorValues = [
  "<",
  "<=",
  ">",
  ">=",
  "==",
  "!=",
  "in",
  "not_in",
  "between",
  "is_null",
  "not_null",
  "matches",
  "unique",
] as const;
export type ThresholdOperator = (typeof ThresholdOperatorValues)[number];

export const FindingConfidenceValues = ["low", "medium", "high"] as const;
export type FindingConfidence = (typeof FindingConfidenceValues)[number];

export const HypothesisStatusValues = [
  "unverified",
  "verifying",
  "supported",
  "refuted",
] as const;
export type HypothesisStatus = (typeof HypothesisStatusValues)[number];

export const WorkflowStatusValues = [
  "open",
  "acknowledged",
  "investigating",
  "resolved",
  "accepted_risk",
] as const;
export type WorkflowStatus = (typeof WorkflowStatusValues)[number];

export const ReasonTypeValues = [
  "evidence",
  "heuristic",
  "business_context_required",
] as const;
export type ReasonType = (typeof ReasonTypeValues)[number];

export const RecommendationStatusValues = [
  "proposed",
  "accepted",
  "rejected",
  "deferred",
  "implemented",
] as const;
export type RecommendationStatus = (typeof RecommendationStatusValues)[number];

export const RuleTypeValues = [
  "not_null",
  "unique",
  "missing_rate_max",
  "greater_than_or_equal",
  "less_than_or_equal",
  "range",
  "valid_domain",
  "type_valid",
  "conditional_not_null",
  "referential_integrity",
  "date_order",
  "outlier_scan",
] as const;
export type RuleType = (typeof RuleTypeValues)[number];

export const ConditionOperatorValues = [
  "equals",
  "not_equals",
  "in",
  "not_in",
  "greater_than",
  "less_than",
] as const;
export type ConditionOperator = (typeof ConditionOperatorValues)[number];

/** CONTRACTS §6.2 — job lifecycle states (SUCCEEDED / FAILED / CANCELLED terminal). */
export const JobStateValues = [
  "QUEUED",
  "RUNNING",
  "CANCEL_REQUESTED",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
] as const;
export type JobState = (typeof JobStateValues)[number];

// ---------------------------------------------------------------------------
// provenance.schema.json (§1.1) + $defs/metricValue
// ---------------------------------------------------------------------------

export interface Provenance {
  method: MetricMethod;
  method_params?: { [key: string]: JsonValue };
  reason?: string;
  computed_at?: string;
  engine_version?: string;
}

export interface MetricValue {
  value?: JsonValue | null;
  provenance: Provenance;
  denominator?: number;
}

// ---------------------------------------------------------------------------
// profiling_run.schema.json (§2)
// ---------------------------------------------------------------------------

export interface SourceSnapshot {
  path?: string;
  size_bytes?: number;
  modified_at?: string;
  content_hash: string;
  [key: string]: unknown;
}

export interface RunDataset {
  dataset_id: string;
  source_kind: SourceKind;
  connector: string;
  source_ref: string;
  source_snapshot: SourceSnapshot;
}

export interface RunConfig {
  config_version: string;
  config_hash: string;
  profile_mode: ProfileMode;
  [key: string]: unknown;
}

export interface RunEngine {
  engine_version: string;
  duckdb_version?: string;
  python_version?: string;
  [key: string]: unknown;
}

export interface RunTiming {
  started_at: string;
  finished_at?: string;
  duration_ms?: number;
}

export interface RunSampling {
  enabled: boolean;
  strategy?: string;
  sample_size?: number;
  seed?: number;
  [key: string]: unknown;
}

export interface RunErrorEntry {
  code: string;
  task?: string;
  message?: string;
  [key: string]: unknown;
}

export interface ProfilingRun {
  schema_version: string;
  run_id: string;
  job_id?: string;
  dataset: RunDataset;
  config: RunConfig;
  engine: RunEngine;
  timing: RunTiming;
  sampling?: RunSampling;
  status: RunStatus;
  result_refs?: { [key: string]: string };
  error_log?: RunErrorEntry[];
  warnings?: string[];
}

// ---------------------------------------------------------------------------
// column_profile.schema.json (§3.2)
// ---------------------------------------------------------------------------

export interface ColumnUnit {
  currency?: string;
  status: UnitStatus;
  [key: string]: unknown;
}

export interface Missingness {
  null_count: number;
  blank_count: number;
  sentinel_count: number;
  missing_rate: MetricValue;
}

export interface Uniqueness {
  distinct_count: MetricValue;
  is_unique_candidate: boolean;
}

export interface NumericStats {
  valid_count?: number;
  min?: MetricValue;
  max?: MetricValue;
  mean?: MetricValue;
  median?: MetricValue;
  p25?: MetricValue;
  p75?: MetricValue;
  p95?: MetricValue;
  p99?: MetricValue;
  stddev?: MetricValue;
}

export interface Histogram {
  bins: Array<number | string>;
  counts: number[];
  provenance: Provenance;
}

export interface OutlierCandidates {
  count: number;
  method: string;
  provenance: Provenance;
  sample_row_refs?: string[];
}

export interface Distribution {
  histogram?: Histogram;
  skewness?: MetricValue;
  outlier_candidates?: OutlierCandidates;
}

export interface Categorical {
  categories?: string[];
  frequencies?: number[];
  rare_categories?: string[];
  label_inconsistencies?: JsonValue[];
  [key: string]: unknown;
}

export interface TemporalRange {
  min?: string;
  max?: string;
}

export interface Temporal {
  range?: TemporalRange | null;
  volume_per_granularity?: { [key: string]: JsonValue } | null;
  gaps?: { [key: string]: JsonValue } | JsonValue[] | null;
  out_of_expected_range?: { [key: string]: JsonValue } | null;
  timezone_source?: string | null;
  [key: string]: unknown;
}

export interface ColumnProfile {
  schema_version: string;
  column: string;
  ordinal: number;
  physical_type: string;
  logical_type: string;
  semantic_type: SemanticType;
  unit?: ColumnUnit | null;
  missingness?: Missingness | null;
  uniqueness?: Uniqueness | null;
  stats?: NumericStats | null;
  distribution?: Distribution | null;
  categorical?: Categorical | null;
  temporal?: Temporal | null;
  visualization_refs?: string[];
  related_metric_refs?: string[];
}

// ---------------------------------------------------------------------------
// dataset_profile.schema.json (§3.1)
// ---------------------------------------------------------------------------

export interface TemporalCoverage {
  column: string;
  min?: string | null;
  max?: string | null;
  provenance: Provenance;
}

export interface DatasetProfile {
  schema_version: string;
  run_id: string;
  row_count?: MetricValue;
  column_count?: number;
  grain?: string | null;
  primary_key_candidates?: string[];
  temporal_coverage?: TemporalCoverage | null;
  source_metadata?: { [key: string]: JsonValue } | null;
  columns: ColumnProfile[];
}

// ---------------------------------------------------------------------------
// quality_check_result.schema.json (§4.1)
// ---------------------------------------------------------------------------

export interface CheckThreshold {
  operator: ThresholdOperator;
  value?: JsonValue | null;
  upper?: JsonValue | null;
}

export interface CheckEvidence {
  denominator?: number | null;
  sample_row_refs?: string[];
  baseline_ref?: string | null;
  [key: string]: unknown;
}

export interface CheckError {
  code: string;
  message?: string;
  [key: string]: unknown;
}

export interface QualityCheckResult {
  schema_version: string;
  run_id: string;
  rule_id: string;
  rule_version: string;
  dimension: QualityDimension;
  status: CheckStatus;
  severity: Severity;
  column?: string | null;
  observed?: MetricValue | null;
  threshold?: CheckThreshold | null;
  failing_record_count?: number | null;
  evidence?: CheckEvidence | null;
  error?: CheckError | null;
  executed_at: string;
}

// ---------------------------------------------------------------------------
// finding_workflow.schema.json (§4.5)
// ---------------------------------------------------------------------------

export interface WorkflowOwnerRef {
  id?: string;
  display?: string;
  [key: string]: unknown;
}

export interface WorkflowComment {
  author: string;
  body: string;
  created_at: string;
  [key: string]: unknown;
}

export interface FindingWorkflow {
  owner?: WorkflowOwnerRef | string | null;
  status: WorkflowStatus;
  comments?: WorkflowComment[];
  resolution_evidence?: JsonValue | null;
  updated_at?: string | null;
}

// ---------------------------------------------------------------------------
// finding.schema.json (§4.2 / §4.4)
// ---------------------------------------------------------------------------

export interface FindingScope {
  column?: string | null;
  affected_records?: number | null;
  period?: { [key: string]: JsonValue } | string | null;
  [key: string]: unknown;
}

export interface FindingFact {
  metric_ref: string;
  value?: JsonValue | null;
  provenance: Provenance;
}

export interface FindingHypothesis {
  text: string;
  status: HypothesisStatus;
  suggested_probe?: string | null;
}

export interface Finding {
  schema_version: string;
  finding_id: string;
  fingerprint: string;
  run_id: string;
  title: string;
  summary: string;
  category: QualityDimension;
  severity: Severity;
  confidence: FindingConfidence;
  scope: FindingScope;
  facts: FindingFact[];
  interpretation: string | null;
  hypotheses: FindingHypothesis[];
  evidence_refs: string[];
  related_finding_ids?: string[];
  recommendation_ids?: string[];
  workflow?: FindingWorkflow | null;
}

// ---------------------------------------------------------------------------
// recommendation.schema.json (§4.3)
// ---------------------------------------------------------------------------

export interface Recommendation {
  schema_version: string;
  recommendation_id: string;
  finding_id: string;
  action: string;
  rationale: string;
  reason_type: ReasonType;
  target_improvement?: string | null;
  do_not?: string[];
  evidence_refs: string[];
  status: RecommendationStatus;
}

// ---------------------------------------------------------------------------
// dataset_config.schema.json (§9)
// ---------------------------------------------------------------------------

export interface ConfigUnitAssertion {
  currency?: string;
  status: UnitStatus;
  [key: string]: unknown;
}

export interface ConfigDataset {
  name: string;
  grain?: string;
  primary_key?: string[];
  units?: { [key: string]: ConfigUnitAssertion };
}

export interface ConfigSampling {
  enabled: boolean;
  strategy?: string;
  size?: number;
  seed?: number;
  [key: string]: unknown;
}

export interface ConfigMissing {
  sentinel_values?: string[];
}

export interface ConfigProfiling {
  mode: ProfileMode;
  sampling?: ConfigSampling;
  sections?: string[];
  missing?: ConfigMissing;
}

export interface RuleCondition {
  column: string;
  operator: ConditionOperator;
  value?: JsonValue | null;
}

export interface DatasetConfigRule {
  id: string;
  rule: RuleType;
  column: string;
  dimension: QualityDimension;
  severity: Severity;
  value?: JsonValue | null;
  threshold?: JsonValue | null;
  condition?: RuleCondition | null;
  business_note?: string;
}

export interface DatasetConfig {
  config_version: number;
  dataset: ConfigDataset;
  profiling: ConfigProfiling;
  quality_checks: DatasetConfigRule[];
  language?: ReportLanguage;
}

// ---------------------------------------------------------------------------
// §5.2 bridge command payloads (M5-T1) — mirror of bridge/envelope.py.
// These are request bodies wrapped in BridgeRequest.payload, and the small
// response shapes the read commands return; they are NOT among the canonical
// frozen root schemas above, so they are validated by the pydantic models on the
// Python side (extra fields forbidden there) rather than by a schema file.
// ---------------------------------------------------------------------------

export interface ProfileStartPayload {
  dataset_id: string;
  source_path: string;
  config: DatasetConfig | { [key: string]: JsonValue };
  profile_mode?: ProfileMode;
  run_id?: string;
  allowed_roots?: string[];
}

export interface JobIdPayload {
  job_id: string;
}

export interface DatasetInspectPayload {
  source_path: string;
  dataset_id?: string;
  run_id?: string;
  allowed_roots?: string[];
}

export interface DatasetPreviewPayload {
  source_path: string;
  dataset_id?: string;
  run_id?: string;
  allowed_roots?: string[];
  limit?: number; // clamp 1..200 (§5.4 preview row cap)
  offset?: number;
  mask_pii?: boolean;
}

export interface PagedResultPayload {
  run_id?: string;
  dataset_id?: string; // exactly one of run_id/dataset_id is required
  limit?: number;
  offset?: number;
}

export interface QualityGetResultsPayload extends PagedResultPayload {
  status?: CheckStatus;
  dimension?: QualityDimension;
}

export interface FindingsGetPayload extends PagedResultPayload {
  severity?: Severity;
  category?: QualityDimension;
}

export interface ReportSummaryPayload {
  run_id?: string;
  dataset_id?: string;
}

/** §5.2 ``report.get_columns`` (ADR-019) — paged §3.2 column bodies. */
export interface ReportGetColumnsPayload extends PagedResultPayload {}

export interface ArtifactOpenPayload {
  run_id: string;
  kind?: string; // exactly one of kind/ref
  ref?: string;
  reveal?: boolean;
}

/** Page envelope shared by the paged result fetches (§5.4). */
export interface Page {
  limit: number;
  offset: number;
  total: number;
}

export interface InspectColumn {
  ordinal: number;
  name: string;
  physical_type: string;
  logical_type?: string;
  semantic_type?: SemanticType;
}

export interface DatasetInspectResult {
  dataset_id: string;
  connector: string;
  source_ref: string;
  size_bytes?: number;
  modified_at?: string;
  content_hash?: string;
  row_id_policy?: string;
  column_count: number;
  columns: InspectColumn[];
  row_count?: number;
  row_count_method?: MetricMethod;
  last_run?: { [key: string]: JsonValue };
}

export interface PreviewColumn {
  name: string;
  physical_type: string;
  logical_type?: string | null;
  masked: boolean; // SECURITY §4: this column's sample values arrived masked
}

export interface DatasetPreviewResult {
  dataset_id: string;
  connector: string;
  columns: PreviewColumn[];
  rows: JsonValue[][];
  offset: number;
  limit: number;
  returned: number;
  total_rows: number;
  masking_applied: string[];
}

export interface QualityGetResultsResult {
  run_id: string;
  items: QualityCheckResult[];
  page: Page;
}

export interface FindingsGetResult {
  run_id: string;
  items: Finding[];
  page: Page;
}

export interface ArtifactOpenResult {
  run_id: string;
  kind?: string;
  ref: string;
  size_bytes: number;
}

/** §5.2 ``report.get_summary`` result — the §3.1 counts-only rollup (§5.4: no bodies). */
export interface ReportSummaryResult {
  run_id: string;
  schema_version?: string;
  job_id?: string;
  dataset_id?: string;
  connector?: string;
  status?: JobState;
  config?: { [key: string]: JsonValue };
  engine_version?: string;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  sampling_enabled?: boolean | null;
  sample_seed?: number | null;
  row_count?: number | null;
  row_count_method?: MetricMethod;
  column_count?: number | null;
  grain?: string | null;
  temporal_coverage?: TemporalCoverage | null;
  check_status_counts?: { [key: string]: number };
  finding_count?: number;
  check_count?: number;
  recommendation_count?: number;
  error_count?: number;
  warning_count?: number;
  result_refs?: { [key: string]: string };
  error_log?: { [key: string]: JsonValue }[];
  [key: string]: unknown;
}

/** §5.2 ``report.get_columns`` result — one paged page of §3.2 ColumnProfiles. */
export interface ReportGetColumnsResult {
  run_id: string;
  items: ColumnProfile[];
  page: Page;
}

// ---------------------------------------------------------------------------
// job_status.schema.json (§5.3 polled status + §6.2 states) — M5-T3.
//
// One shape serves BOTH progress transports (§5.5): the UI polls
// `profile.get_status` over the bridge, and the host may additionally push the
// very same object through `window.__dpeJobStatus` via evaluate_js. Because the
// payload is identical, UI code never cares which transport delivered it —
// polling stays authoritative and a push is only a hint. `error_code` carries a
// §8 registry code on FAILED; `result` is the bounded success output and still
// obeys the 1 MiB cap (§5.4) — larger payloads go through paging/artifact.open.
// ---------------------------------------------------------------------------

export interface JobProgress {
  completed_steps: number;
  total_steps: number;
  current_step?: string;
}

export interface JobStatus {
  job_id: string;
  status: JobState;
  progress: JobProgress;
  error_code?: string;
  result?: { [key: string]: JsonValue };
}
