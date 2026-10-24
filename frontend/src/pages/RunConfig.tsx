/**
 * M6-T2 — Run Configuration screen (CONTRACTS §9).
 *
 * After the dataset is confirmed on the Dataset screen, this screen collects
 * the run parameters — profile mode, optional sampling, narrative language and
 * quality-check rules (or a full uploaded §9 YAML/JSON config) — validates the
 * assembled document CLIENT-SIDE (mirror of the facade's `_validate_config`
 * hints) and only then offers ``profile.start``. The server stays the
 * authority: an invalid config is still rejected there with CONFIG_INVALID
 * before any worker spawns (AGENTS §5). No HTTP, no new bridge command
 * (ADR-005, §5.2): one accepted envelope yields the §5.3 job id, which is
 * handed to the shared store and tracked live on /progress (M6-T3).
 */
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { BridgeError, bridgeAvailable, bridgeStartJob } from "../bridge/api";
import {
  BRIDGE_MESSAGE_CAP_BYTES,
  buildConfigFromForm,
  parseConfigDocument,
  utf8ByteLength,
  validateDatasetConfig,
  type FormRuleInput,
} from "../config/validation";
import { setStartedJob, useSelectedDataset } from "../state/selection";
import type {
  ConditionOperator,
  DatasetConfig,
  ProfileMode,
  ProfileStartPayload,
  QualityDimension,
  RuleType,
  Severity,
} from "../types/contracts";
import {
  ConditionOperatorValues,
  ProfileModeValues,
  QualityDimensionValues,
  RuleTypeValues,
  SeverityValues,
} from "../types/contracts";

/** One starting rule so the minItems=1 requirement is visible, not hidden. */
const EMPTY_RULE: FormRuleInput = {
  rule: "not_null",
  column: "",
  dimension: "completeness",
  severity: "high",
  valueText: "",
  conditionColumn: "",
  conditionOperator: "equals",
  conditionValueText: "",
};

interface UploadState {
  fileName: string;
  doc: DatasetConfig | null;
  error: string | null;
  errors: string[];
}

export default function RunConfig() {
  const dataset = useSelectedDataset();
  const hasBridge = bridgeAvailable();

  // --- form state ---
  const [mode, setMode] = useState<ProfileMode>("standard");
  const [samplingOn, setSamplingOn] = useState(false);
  const [strategy, setStrategy] = useState("reservoir");
  const [sizeText, setSizeText] = useState("100000");
  const [seedText, setSeedText] = useState("20261024");
  const [language, setLanguage] = useState<"" | "en" | "id">("");
  const [rules, setRules] = useState<FormRuleInput[]>([EMPTY_RULE]);

  // --- upload state ---
  const [upload, setUpload] = useState<UploadState | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // --- start ---
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [acceptedJobId, setAcceptedJobId] = useState<string | null>(null);

  const uploadActive = upload !== null && upload.doc !== null && upload.error === null;

  // --- assembled config + validation errors ---
  const { config, errors } = useMemo(() => {
    if (uploadActive && upload?.doc) {
      return { config: upload.doc, errors: upload.errors };
    }
    if (!dataset) return { config: null as DatasetConfig | null, errors: [] as string[] };
    const built = buildConfigFromForm({
      datasetName: dataset.datasetId,
      mode,
      language,
      sampling: { enabled: samplingOn, strategyText: strategy, sizeText, seedText },
      rules,
    });
    return {
      config: built.config as DatasetConfig,
      errors: [...built.errors, ...validateDatasetConfig(built.config)],
    };
  }, [uploadActive, upload, dataset, mode, language, samplingOn, strategy, sizeText, seedText, rules]);

  const canStart =
    !!dataset && hasBridge && !!config && errors.length === 0 && !starting;

  // --- handlers ---

  function patchRule(index: number, patch: Partial<FormRuleInput>) {
    setRules((rs) => rs.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setUpload(null);
    try {
      const text = await file.text();
      if (utf8ByteLength(text) > BRIDGE_MESSAGE_CAP_BYTES) {
        setUpload({ fileName: file.name, doc: null, error: `file is larger than the ${BRIDGE_MESSAGE_CAP_BYTES / 1024} KiB bridge message cap (§5.4)`, errors: [] });
        return;
      }
      const parsed = parseConfigDocument(file.name, text);
      if (!parsed.ok || parsed.doc === undefined) {
        setUpload({ fileName: file.name, doc: null, error: parsed.error ?? "parse failed", errors: [] });
        return;
      }
      const errs = validateDatasetConfig(parsed.doc);
      setUpload({
        fileName: file.name,
        // The client mirror is a UX hint only; the authoritative §9 validation
        // runs server-side on profile.start, so the shape assertion lives here.
        doc: errs.length === 0 ? (parsed.doc as unknown as DatasetConfig) : null,
        error: null,
        errors: errs,
      });
    } catch (e) {
      setUpload({ fileName: file.name, doc: null, error: `could not read file: ${String(e)}`, errors: [] });
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function handleStart() {
    if (!dataset || !config) return;
    setStarting(true);
    setStartError(null);
    setAcceptedJobId(null);
    const payload: ProfileStartPayload = {
      dataset_id: dataset.datasetId,
      source_path: dataset.sourcePath,
      config,
      // The mode pick always wins over an uploaded profiling.mode: the worker
      // folds profile_mode into the document before running (facade
      // _validate_config / profile_job._load_config), so what is validated is
      // what runs and the two can never disagree silently.
      profile_mode: mode,
    };
    const size = utf8ByteLength(JSON.stringify(payload));
    if (size > BRIDGE_MESSAGE_CAP_BYTES) {
      setStartError(`request is ${size} bytes and exceeds the 1 MiB bridge message cap (§5.4) — slim down the config`);
      setStarting(false);
      return;
    }
    try {
      const { jobId } = await bridgeStartJob("profile.start", payload);
      setAcceptedJobId(jobId);
      setStartedJob({
        jobId,
        datasetId: dataset.datasetId,
        sourcePath: dataset.sourcePath,
        startedAt: new Date().toISOString(),
      });
    } catch (e) {
      // CONFIG_INVALID here means the server-side authority rejected what the
      // client mirror passed — show it verbatim; never start around it.
      setStartError(e instanceof BridgeError ? `${e.code}: ${e.message}` : String(e));
    } finally {
      setStarting(false);
    }
  }

  // --- render ---

  if (!dataset) {
    return (
      <section className="run-config">
        <h1>Run Configuration</h1>
        <div className="panel">
          <p>No dataset selected yet.</p>
          <p className="hint">
            Confirm a file on the <Link to="/">Dataset</Link> screen first — its
            schema is needed to configure the run.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="run-config">
      <h1>Run Configuration</h1>
      {!hasBridge && (
        <div className="banner-warn">
          Desktop bridge not detected — the run cannot be started from a plain
          browser. Run inside the app shell.
        </div>
      )}

      <div className="panel">
        <h2>Dataset</h2>
        <dl className="meta-grid">
          <dt>File</dt>
          <dd className="mono">{dataset.sourcePath}</dd>
          <dt>Dataset ID</dt>
          <dd>{dataset.datasetId}</dd>
          <dt>Columns</dt>
          <dd>{dataset.columns.length}</dd>
        </dl>
        <p className="hint">
          Different file? Re-inspect it on the <Link to="/">Dataset</Link> screen.
        </p>
      </div>

      <div className="panel">
        <h2>Profile Mode</h2>
        <div className="mode-options">
          {ProfileModeValues.map((m) => (
            <label key={m} className="radio">
              <input type="radio" name="mode" value={m} checked={mode === m} onChange={() => setMode(m)} />
              <span>{m}</span>
            </label>
          ))}
        </div>
        <p className="hint">Cost-class gate (ENGINE §2.1): quick &lt; standard &lt; deep.</p>
      </div>

      <div className={uploadActive ? "panel panel-disabled" : "panel"}>
        <h2>Sampling</h2>
        <label className="radio">
          <input
            type="checkbox"
            checked={samplingOn}
            disabled={uploadActive}
            onChange={(e) => setSamplingOn(e.target.checked)}
          />
          <span>Profile a bounded random sample instead of the full dataset</span>
        </label>
        {samplingOn && !uploadActive && (
          <>
            <div className="field-row">
              <span className="field-label">Strategy</span>
              <input className="path-input short" type="text" value={strategy} onChange={(e) => setStrategy(e.target.value)} spellCheck={false} />
            </div>
            <div className="field-row">
              <span className="field-label">Sample size</span>
              <input className="num-input" type="number" min={1} step={1} value={sizeText} onChange={(e) => setSizeText(e.target.value)} />
            </div>
            <div className="field-row">
              <span className="field-label">Seed</span>
              <input className="num-input" type="number" step={1} value={seedText} onChange={(e) => setSeedText(e.target.value)} />
            </div>
            <p className="hint">
              Sampling requires a seed — every sampled metric carries
              method=sampled + the seed so it is never presented as exact (ADR-015).
            </p>
          </>
        )}
        {uploadActive && (
          <p className="hint">Ignored while an uploaded config is active — sampling comes from its profiling block.</p>
        )}
      </div>

      <div className={uploadActive ? "panel panel-disabled" : "panel"}>
        <h2>Report Language</h2>
        <div className="field-row">
          <span className="field-label">Narratives</span>
          <select value={language} disabled={uploadActive} onChange={(e) => setLanguage(e.target.value as "" | "en" | "id")}>
            <option value="">default (en)</option>
            <option value="en">en — English</option>
            <option value="id">id — Bahasa Indonesia</option>
          </select>
        </div>
        <p className="hint">
          Selects recommendation-narrative templates only; metric labels stay
          English (ADR-018). Unset keeps the config byte-identical to pre-ADR-018 runs.
        </p>
      </div>

      <div className={uploadActive ? "panel panel-disabled" : "panel"}>
        <h2>Quality Checks</h2>
        {uploadActive ? (
          <p className="hint">Ignored while an uploaded config is active — its quality_checks are used verbatim.</p>
        ) : (
          <>
            {rules.map((r, i) => (
              <div className="rule-block" key={i}>
                <div className="rule-row">
                  <select value={r.column} onChange={(e) => patchRule(i, { column: e.target.value })}>
                    <option value="">(column)</option>
                    {dataset.columns.map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                  <select value={r.rule} onChange={(e) => patchRule(i, { rule: e.target.value as RuleType })}>
                    {RuleTypeValues.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                  <select value={r.dimension} onChange={(e) => patchRule(i, { dimension: e.target.value as QualityDimension })}>
                    {QualityDimensionValues.map((d) => (
                      <option key={d} value={d}>{d}</option>
                    ))}
                  </select>
                  <select value={r.severity} onChange={(e) => patchRule(i, { severity: e.target.value as Severity })}>
                    {SeverityValues.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                  <input
                    className="path-input"
                    type="text"
                    placeholder="value — JSON or text (required for threshold rules)"
                    value={r.valueText}
                    onChange={(e) => patchRule(i, { valueText: e.target.value })}
                    spellCheck={false}
                  />
                  <button
                    className="btn-sm"
                    onClick={() => setRules((rs) => (rs.length > 1 ? rs.filter((_, j) => j !== i) : rs))}
                    disabled={rules.length === 1}
                    title={rules.length === 1 ? "At least one rule is required (§9 minItems=1)" : "Remove rule"}
                  >
                    ✕
                  </button>
                </div>
                {r.rule === "conditional_not_null" && (
                  <div className="rule-row indent">
                    <span className="field-label">when</span>
                    <select value={r.conditionColumn} onChange={(e) => patchRule(i, { conditionColumn: e.target.value })}>
                      <option value="">(condition column)</option>
                      {dataset.columns.map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                    <select value={r.conditionOperator} onChange={(e) => patchRule(i, { conditionOperator: e.target.value as ConditionOperator })}>
                      {ConditionOperatorValues.map((op) => (
                        <option key={op} value={op}>{op}</option>
                      ))}
                    </select>
                    <input
                      className="path-input"
                      type="text"
                      placeholder="condition value"
                      value={r.conditionValueText}
                      onChange={(e) => patchRule(i, { conditionValueText: e.target.value })}
                      spellCheck={false}
                    />
                  </div>
                )}
              </div>
            ))}
            <div>
              <button className="btn-sm" onClick={() => setRules((rs) => [...rs, { ...EMPTY_RULE }])}>
                + Add rule
              </button>
            </div>
            <p className="hint">
              Rule ids are derived from column + rule type (deterministic, stable across runs).
              A profile always needs at least one quality check (§9).
            </p>
          </>
        )}
      </div>

      <div className="panel">
        <h2>Upload §9 Config (optional)</h2>
        <div className="field-row">
          <input
            ref={fileRef}
            type="file"
            accept=".yaml,.yml,.json"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
          {upload && (
            <button className="btn-sm" onClick={() => setUpload(null)}>Clear</button>
          )}
        </div>
        {upload && (
          <div className="upload-status">
            <span className="mono">{upload.fileName}</span>
            {upload.error && <span className="upload-error">— {upload.error}</span>}
            {!upload.error && upload.errors.length === 0 && upload.doc && (
              <span>
                {" "}
                — parsed OK: {upload.doc.quality_checks.length} rule
                {upload.doc.quality_checks.length === 1 ? "" : "s"}, config profiling.mode=
                {upload.doc.profiling.mode}
                {upload.doc.profiling.sampling?.enabled ? " (sampling on)" : ""}
                . The mode you pick above still overrides profiling.mode.
              </span>
            )}
            {!upload.error && upload.errors.length > 0 && (
              <span> — rejected; fix the errors below or clear the upload.</span>
            )}
          </div>
        )}
        <p className="hint">
          A full §9 DatasetConfig as YAML (or JSON). It replaces the form
          panels above; invalid documents are rejected before profiling starts
          (unknown keys are hard errors, AGENTS §5).
        </p>
      </div>

      {errors.length > 0 && (
        <div className="panel">
          <h2>Validation Issues</h2>
          <ul className="config-errors">
            {errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="panel">
        <h2>Start</h2>
        <div className="field-row">
          <button className="btn" onClick={handleStart} disabled={!canStart}>
            {starting ? "Starting…" : "Start Profile"}
          </button>
          {!hasBridge && <span className="hint">Bridge unavailable — start only works inside the app shell.</span>}
        </div>
        {startError && <div className="error-box">{startError}</div>}
        {acceptedJobId && (
          <div className="banner-ok">
            Profile job accepted — <code>{acceptedJobId}</code>. Track it live on
            the <Link to="/progress">Progress</Link> screen.
          </div>
        )}
      </div>
    </section>
  );
}
