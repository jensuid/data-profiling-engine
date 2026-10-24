/**
 * M6-T2 — client-side §9 DatasetConfig handling (CONTRACTS §9).
 *
 * Three jobs:
 * 1. :func:`parseConfigDocument` — turn an uploaded YAML/JSON file into a
 *    plain object (JSON ⊂ YAML, same rule the CLI applies with
 *    ``yaml.safe_load``; the bridge only carries the parsed JSON document,
 *    never file text or a path — the worker never reads the user's file).
 * 2. :func:`validateDatasetConfig` — a hand-mirrored client-side check of
 *    ``dataset_config.schema.json`` so the user sees validation errors *while
 *    configuring*. It mirrors the ``_validate_config`` hints of the facade
 *    (facade.py) — it is a UX convenience, NOT a second authority: the
 *    authoritative schema + pydantic validation still runs server-side and
 *    ``profile.start`` rejects an invalid document with ``CONFIG_INVALID``
 *    before any worker spawns (AGENTS §5).
 * 3. :func:`buildConfigFromForm` — assemble a §9 document from the screen's
 *    mode/sampling/rules form (``quality_checks`` is minItems=1, so a run
 *    always carries at least one rule).
 */
import { load as yamlLoad } from "js-yaml";
import type {
  ConditionOperator,
  DatasetConfig,
  DatasetConfigRule,
  JsonValue,
  ProfileMode,
  QualityDimension,
  RuleType,
  Severity,
} from "../types/contracts";
import {
  ConditionOperatorValues,
  ProfileModeValues,
  QualityDimensionValues,
  ReportLanguageValues,
  RuleTypeValues,
  SeverityValues,
  UnitStatusValues,
} from "../types/contracts";

/** CONTRACTS §5.4 — mirror of ``bridge/envelope.PAYLOAD_CAP_BYTES``; the UI
 * pre-checks a start request against it so an oversized config fails locally
 * with a clear message instead of as ``BRIDGE_PAYLOAD_TOO_LARGE`` round-trip. */
export const BRIDGE_MESSAGE_CAP_BYTES = 1024 * 1024;

export function utf8ByteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

// ---------------------------------------------------------------------------
// 1. Upload parsing
// ---------------------------------------------------------------------------

export interface ParseOutcome {
  ok: boolean;
  doc?: JsonValue;
  error?: string;
}

/** Parse one uploaded config file (.yaml/.yml as YAML, .json as JSON). */
export function parseConfigDocument(fileName: string, text: string): ParseOutcome {
  let raw: unknown;
  try {
    raw = /\.json$/i.test(fileName) ? JSON.parse(text) : yamlLoad(text);
  } catch (e) {
    // js-yaml messages are multi-line and quote source lines; keep the first
    // line only — the full text never belongs in the UI error list.
    const msg = e instanceof Error ? e.message : String(e);
    return { ok: false, error: `parse failed: ${msg.split("\n")[0].slice(0, 200)}` };
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, error: "document root must be a mapping/object" };
  }
  return { ok: true, doc: raw as JsonValue };
}

// ---------------------------------------------------------------------------
// 2. Client-side §9 validation (mirror of dataset_config.schema.json)
// ---------------------------------------------------------------------------

const TOP_KEYS = ["config_version", "language", "dataset", "profiling", "quality_checks"];
const TOP_REQUIRED = ["config_version", "dataset", "profiling", "quality_checks"];
const DATASET_KEYS = ["name", "grain", "primary_key", "units"];
const PROFILING_KEYS = ["mode", "sampling", "sections", "missing"];
const MISSING_KEYS = ["sentinel_values"];
const RULE_KEYS = [
  "id",
  "rule",
  "column",
  "dimension",
  "severity",
  "value",
  "threshold",
  "condition",
  "business_note",
];
const CONDITION_KEYS = ["column", "operator", "value"];
/** Rules whose schema conditional requires a `value` or `threshold` parameter. */
const THRESHOLD_RULES = new Set([
  "missing_rate_max",
  "greater_than_or_equal",
  "less_than_or_equal",
  "range",
  "valid_domain",
]);

type Errs = string[];

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function nonEmptyString(v: unknown): boolean {
  return typeof v === "string" && v.length > 0;
}

function uniqueNonEmptyStringArray(v: unknown): boolean {
  return (
    Array.isArray(v) &&
    v.length > 0 &&
    v.every(nonEmptyString) &&
    new Set(v as string[]).size === v.length
  );
}

function checkUnknownKeys(obj: Record<string, unknown>, allowed: string[], path: string, errs: Errs): void {
  for (const key of Object.keys(obj)) {
    if (!allowed.includes(key)) {
      errs.push(`${path}: unknown key "${key}" is not allowed (§9 rejects unknown keys)`);
    }
  }
}

function validateRule(path: string, rule: unknown, errs: Errs): void {
  if (!isPlainObject(rule)) {
    errs.push(`${path}: each quality check must be an object`);
    return;
  }
  checkUnknownKeys(rule, RULE_KEYS, path, errs);
  if (!nonEmptyString(rule.id)) errs.push(`${path}.id: required non-empty string`);
  const ruleType = rule.rule;
  if (typeof ruleType !== "string" || !RuleTypeValues.includes(ruleType as RuleType)) {
    errs.push(`${path}.rule: must be one of the closed MVP catalog (${RuleTypeValues.join(", ")})`);
  }
  if (!nonEmptyString(rule.column)) errs.push(`${path}.column: required non-empty string`);
  if (typeof rule.dimension !== "string" || !QualityDimensionValues.includes(rule.dimension as QualityDimension)) {
    errs.push(`${path}.dimension: required (§4.1 scorecard); must be one of ${QualityDimensionValues.join(", ")}`);
  }
  if (typeof rule.severity !== "string" || !SeverityValues.includes(rule.severity as Severity)) {
    errs.push(`${path}.severity: must be one of ${SeverityValues.join(", ")}`);
  }
  if ("business_note" in rule && typeof rule.business_note !== "string") {
    errs.push(`${path}.business_note: must be a string`);
  }
  // `value` is unconstrained by the schema ({}); `threshold` may be object /
  // number / string / boolean / null but NEVER an array (schema type list).
  if ("threshold" in rule && Array.isArray(rule.threshold)) {
    errs.push(`${path}.threshold: must not be an array`);
  }
  const cond = rule.condition;
  const hasCondition = isPlainObject(cond);
  if ("condition" in rule) {
    if (!hasCondition) {
      errs.push(`${path}.condition: must be an object`);
    } else {
      const cpath = `${path}.condition`;
      checkUnknownKeys(cond, CONDITION_KEYS, cpath, errs);
      if (!nonEmptyString(cond.column)) errs.push(`${cpath}.column: required non-empty string`);
      const op = cond.operator;
      if (typeof op !== "string" || !ConditionOperatorValues.includes(op as ConditionOperator)) {
        errs.push(`${cpath}.operator: must be one of ${ConditionOperatorValues.join(", ")}`);
      }
      if (!("value" in cond)) errs.push(`${cpath}.value: required key (any JSON value)`);
    }
  }
  if (ruleType === "conditional_not_null" && !hasCondition) {
    errs.push(`${path}: rule "conditional_not_null" requires a condition`);
  }
  if (typeof ruleType === "string" && THRESHOLD_RULES.has(ruleType) && !("value" in rule) && !("threshold" in rule)) {
    errs.push(`${path}: rule "${ruleType}" requires a value or threshold`);
  }
}

/** Validate a §9 config document against the schema rules, client-side.
 * Returns a (possibly empty) list of human-readable field-path errors. */
export function validateDatasetConfig(doc: unknown): string[] {
  const errs: Errs = [];
  if (!isPlainObject(doc)) {
    return ["config: root must be a mapping/object"];
  }
  checkUnknownKeys(doc, TOP_KEYS, "config", errs);
  for (const key of TOP_REQUIRED) {
    if (!(key in doc)) errs.push(`config: required key "${key}" is missing`);
  }
  if ("config_version" in doc) {
    const cv = doc.config_version;
    if (typeof cv !== "number" || !Number.isInteger(cv) || cv < 1) {
      errs.push("config.config_version: must be an integer ≥ 1");
    }
  }
  if ("language" in doc && typeof doc.language !== "string") {
    errs.push("config.language: must be a string");
  } else if ("language" in doc && !ReportLanguageValues.includes(doc.language as (typeof ReportLanguageValues)[number])) {
    errs.push(`config.language: must be one of ${ReportLanguageValues.join(" | ")} (ADR-018)`);
  }

  // dataset block
  if ("dataset" in doc) {
    if (!isPlainObject(doc.dataset)) {
      errs.push("config.dataset: must be an object");
    } else {
      const ds = doc.dataset;
      checkUnknownKeys(ds, DATASET_KEYS, "config.dataset", errs);
      if (!nonEmptyString(ds.name)) errs.push("config.dataset.name: required non-empty string");
      if ("grain" in ds && !nonEmptyString(ds.grain)) errs.push("config.dataset.grain: must be a non-empty string");
      if ("primary_key" in ds && !uniqueNonEmptyStringArray(ds.primary_key)) {
        errs.push("config.dataset.primary_key: must be a non-empty array of unique non-empty strings");
      }
      if ("units" in ds) {
        if (!isPlainObject(ds.units)) {
          errs.push("config.dataset.units: must be an object keyed by column name");
        } else {
          for (const [col, unit] of Object.entries(ds.units)) {
            if (!isPlainObject(unit)) {
              errs.push(`config.dataset.units.${col}: must be an object`);
              continue;
            }
            if (typeof unit.status !== "string" || !UnitStatusValues.includes(unit.status as (typeof UnitStatusValues)[number])) {
              errs.push(`config.dataset.units.${col}.status: must be one of ${UnitStatusValues.join(", ")} (never silently asserted)`);
            }
            if ("currency" in unit && !nonEmptyString(unit.currency)) {
              errs.push(`config.dataset.units.${col}.currency: must be a non-empty string`);
            }
          }
        }
      }
    }
  }

  // profiling block
  if ("profiling" in doc) {
    if (!isPlainObject(doc.profiling)) {
      errs.push("config.profiling: must be an object");
    } else {
      const p = doc.profiling;
      checkUnknownKeys(p, PROFILING_KEYS, "config.profiling", errs);
      if (typeof p.mode !== "string" || !ProfileModeValues.includes(p.mode as ProfileMode)) {
        errs.push(`config.profiling.mode: required; must be one of ${ProfileModeValues.join(" | ")}`);
      }
      if ("sampling" in p) {
        if (!isPlainObject(p.sampling)) {
          errs.push("config.profiling.sampling: must be an object");
        } else {
          const s = p.sampling; // OPEN block per schema — unknown keys are allowed here
          if (typeof s.enabled !== "boolean") {
            errs.push("config.profiling.sampling.enabled: required boolean");
          } else if (s.enabled) {
            // Reproducibility lock (§9 / §2 / ADR-015): sampled ⇒ seed required.
            if (!nonEmptyString(s.strategy)) errs.push("config.profiling.sampling.strategy: required when sampling is enabled");
            if (typeof s.size !== "number" || !Number.isInteger(s.size) || s.size < 1) {
              errs.push("config.profiling.sampling.size: must be an integer ≥ 1 when sampling is enabled");
            }
            if (typeof s.seed !== "number" || !Number.isInteger(s.seed)) {
              errs.push("config.profiling.sampling.seed: must be an integer when sampling is enabled (sampled metrics require a reproducible seed, ADR-015)");
            }
          }
        }
      }
      if ("sections" in p && !uniqueNonEmptyStringArray(p.sections)) {
        errs.push("config.profiling.sections: must be a non-empty array of unique non-empty strings");
      }
      if ("missing" in p) {
        if (!isPlainObject(p.missing)) {
          errs.push("config.profiling.missing: must be an object");
        } else {
          checkUnknownKeys(p.missing, MISSING_KEYS, "config.profiling.missing", errs);
          if ("sentinel_values" in p.missing && !uniqueNonEmptyStringArray(p.missing.sentinel_values)) {
            errs.push("config.profiling.missing.sentinel_values: must be a non-empty array of unique non-empty strings (ADR-017)");
          }
        }
      }
    }
  }

  // quality_checks
  if ("quality_checks" in doc) {
    if (!Array.isArray(doc.quality_checks)) {
      errs.push("config.quality_checks: must be an array");
    } else {
      if (doc.quality_checks.length < 1) {
        errs.push("config.quality_checks: at least one rule is required (§9 minItems=1)");
      }
      doc.quality_checks.forEach((rule, i) => validateRule(`config.quality_checks[${i}]`, rule, errs));
    }
  }

  return errs;
}

// ---------------------------------------------------------------------------
// 3. Form → §9 document
// ---------------------------------------------------------------------------

export interface FormSamplingInput {
  enabled: boolean;
  strategyText: string;
  sizeText: string;
  seedText: string;
}

export interface FormRuleInput {
  rule: RuleType;
  column: string;
  dimension: QualityDimension;
  severity: Severity;
  /** Optional rule parameter; parsed as JSON when possible, else literal text. */
  valueText: string;
  conditionColumn: string;
  conditionOperator: ConditionOperator;
  conditionValueText: string;
}

/** Parse a free-text parameter the way §9 means it: JSON first ("0.05",
 * ["A","B"], true), plain string otherwise ("Closed Won"). */
export function parseValueText(text: string): JsonValue {
  try {
    return JSON.parse(text) as JsonValue;
  } catch {
    return text;
  }
}

function ruleIdFor(input: FormRuleInput, used: Set<string>): string {
  const base = `${(input.column || "rule").toLowerCase().replace(/[^a-z0-9_]+/g, "_")}_${input.rule}`;
  let id = base;
  let n = 2;
  while (used.has(id)) id = `${base}_${n++}`;
  used.add(id);
  return id;
}

export interface FormConfigInput {
  datasetName: string;
  mode: ProfileMode;
  language: "" | "en" | "id";
  sampling: FormSamplingInput;
  rules: FormRuleInput[];
}

/** Assemble the §9 document from the screen form. Field-level problems
 * (unparseable numbers, missing column) come back as errors ALONGSIDE a best
 * effort document — the screen shows the merged error list and only enables
 * Start when it is empty; the document is additionally run through
 * :func:`validateDatasetConfig` by the caller for full-schema parity. */
export function buildConfigFromForm(input: FormConfigInput): { config: DatasetConfig; errors: string[] } {
  const errors: Errs = [];

  // sampling — disabled emits the minimal {enabled:false} (strategy/size/seed
  // are only required when enabled=true, matching the schema conditional).
  let sampling: DatasetConfig["profiling"]["sampling"];
  if (input.sampling.enabled) {
    const size = Number(input.sampling.sizeText);
    const seed = Number(input.sampling.seedText);
    if (!input.sampling.strategyText.trim()) {
      errors.push("profiling.sampling.strategy: required when sampling is enabled");
    }
    if (!Number.isInteger(size) || size < 1) {
      errors.push("profiling.sampling.size: must be a whole number ≥ 1");
    }
    if (!Number.isInteger(seed)) {
      errors.push("profiling.sampling.seed: must be a whole number — sampled metrics require a reproducible seed (ADR-015)");
    }
    sampling = {
      enabled: true,
      strategy: input.sampling.strategyText.trim() || "reservoir",
      size,
      seed,
    };
  } else {
    sampling = { enabled: false };
  }

  // rules — ids are derived deterministically from (column, rule type).
  const used = new Set<string>();
  const rules: DatasetConfigRule[] = input.rules.map((r, i) => {
    if (!r.column) errors.push(`rule ${i + 1}: select the column it checks`);
    const entry: DatasetConfigRule = {
      id: ruleIdFor(r, used),
      rule: r.rule,
      column: r.column || "-",
      dimension: r.dimension,
      severity: r.severity,
    };
    if (r.valueText.trim() !== "") entry.value = parseValueText(r.valueText.trim());
    if (r.rule === "conditional_not_null") {
      if (!r.conditionColumn) errors.push(`rule ${i + 1}: conditional_not_null needs the condition column`);
      entry.condition = {
        column: r.conditionColumn || "-",
        operator: r.conditionOperator,
        value: r.conditionValueText.trim() === "" ? null : parseValueText(r.conditionValueText.trim()),
      };
    }
    return entry;
  });

  const config: DatasetConfig = {
    config_version: 3,
    dataset: { name: input.datasetName || "dataset" },
    profiling: { mode: input.mode, sampling },
    quality_checks: rules,
  };
  // Omit `language` when unset so the document matches the ADR-018
  // omitted-when-unset policy (keeps config_hash stable for default runs).
  if (input.language !== "") config.language = input.language;

  return { config, errors };
}
