/**
 * M6-T4/T5/T6 — Report View: Dataset Overview (§3.1) + Schema & Column Profiling
 * (§3.2) + Visualizations (§3.3) + Quality Assessment (§3.4) + Findings &
 * Recommendations (§3.4/§4.2–4.3) + Export actions (§3.5).
 *
 * Drives the report reads through the typed bridge client:
 *   • ``report.get_summary``   — the counts-only §3.1 rollup + check scorecard.
 *   • ``report.get_columns``   — the §3.2 ColumnProfile bodies, paged (ADR-019).
 *   • ``quality.get_results``  — the §4.1 check table, paged + filtered (§3.4).
 *   • ``findings.get``         — the §4.2 findings, paged + severity/category filtered.
 *   • ``recommendations.get``  — the §4.3 recommendation bodies (ADR-020).
 *   • ``artifact.open``        — the §3.5 exports (ack + reveal, no bytes cross).
 *
 * The §3.3 charts consume the SAME ``report.get_columns`` bodies — histogram,
 * categorical frequency, missing-rate bar, IQR box — rendered client-side from
 * the stored metrics via the vendored Plotly bundle; NO new command and nothing
 * recomputed here (§10). Parity with ``reporting/html_report.py`` chart shapes.
 *
 * The screen is addressed by the dataset the user last ran (from the shared
 * selection store); the store resolves the *latest run*, and the summary supplies
 * that ``run_id`` so the column/quality/finding/recommendation pages are pinned to
 * the same run (ADR-019/ADR-020 — the UI ``StartedJob`` store deliberately does NOT
 * carry ``run_id``).
 *
 * Rules honoured here: every numeric carries its ADR-015 ``method`` (an
 * approximate value shows a "≈" badge, a sampled one a "sampled" badge, a
 * ``not_computed`` one shows its reason and never a silent null/zero); a unit not
 * confirmed by config is labelled ``assumed``/``unknown``, never silently asserted
 * (§3.2.1); FAIL≠ERROR is kept distinct in the quality table (§4.1). Findings keep
 * the §7 epistemic separation: ``facts`` render as evidence (with their own
 * provenance), ``interpretation``/``hypotheses`` render separately and a hypothesis
 * is never folded into a fact; a recommendation renders as its own §4.3 block cited
 * by the finding's ``recommendation_ids`` — never merged into a fact.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BridgeError, bridgeAvailable, bridgeSend } from "../bridge/api";
import { useSelectedDataset, useStartedJob } from "../state/selection";
import PlotlyChart from "../charts/PlotlyChart";
import { chartsForColumn, missingnessChart, type PlotlySpec } from "../charts/plotly";
import type {
  ArtifactOpenPayload,
  ArtifactOpenResult,
  CheckStatus,
  ColumnProfile,
  Finding,
  FindingsGetPayload,
  FindingsGetResult,
  MetricValue,
  Provenance,
  QualityCheckResult,
  QualityDimension,
  QualityGetResultsPayload,
  QualityGetResultsResult,
  Recommendation,
  RecommendationsGetPayload,
  RecommendationsGetResult,
  ReportGetColumnsPayload,
  ReportGetColumnsResult,
  ReportSummaryPayload,
  ReportSummaryResult,
  Severity,
} from "../types/contracts";
import {
  CheckStatusValues,
  QualityDimensionValues,
  SeverityValues,
} from "../types/contracts";

// Column page size — bounded so a page stays well under the §5.4 1 MiB cap; a
// wide/high-cardinality run loads more on demand rather than tripping it.
const COLUMN_PAGE_SIZE = 20;

// Quality-check page size (§3.4) — same bounded-fetch rule as the columns.
const QUALITY_PAGE_SIZE = 25;

// Findings page size (§4.2) — a finding carries free-text facts/interpretation/
// hypotheses, so a smaller page keeps each response well under the §5.4 1 MiB cap.
const FINDING_PAGE_SIZE = 10;

// §3.5 MVP export artifacts, in the render order the report surfaces them. Each
// maps to an ``artifact.open`` ``kind`` the run records in its ``result_refs``.
// No bytes cross — the command acks + reveals the file through the OS viewer.
const EXPORT_KINDS: { kind: string; label: string; hint: string }[] = [
  { kind: "html_report", label: "HTML report", hint: "Human-readable report" },
  { kind: "profile_json", label: "JSON result", hint: "Machine-readable full result" },
  { kind: "issue_csv", label: "CSV issue export", hint: "Issue list for follow-up" },
];

type LoadStatus = "idle" | "loading" | "ready" | "error";

function errText(e: unknown): string {
  return e instanceof BridgeError ? `${e.code}: ${e.message}` : String(e);
}

// --- ADR-015 provenance-aware value rendering ---------------------------------

/** Format one raw JSON metric value for display (numbers grouped, null-safe). */
function formatValue(value: Jsonish, unit?: string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    const grouped = value.toLocaleString(undefined, { maximumFractionDigits: 6 });
    return unit ? `${unit}${grouped}` : grouped;
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

type Jsonish = string | number | boolean | null | undefined;

/**
 * Render a §3.2 MetricValue with its provenance badge. A ``not_computed`` value
 * shows "n/c" + the reason (never a fabricated 0), an ``approximate`` value a "≈"
 * badge, a ``sampled`` value a "sampled" badge (ADR-015 — no sampled/approximate
 * value is presented as exact).
 */
function MetricCell({ metric, unit }: { metric?: MetricValue | null; unit?: string }) {
  if (!metric) return <span className="muted">—</span>;
  const method = metric.provenance?.method;
  if (method === "not_computed") {
    return (
      <span className="metric-cell">
        <span className="method-badge method-not_computed" title={metric.provenance.reason ?? ""}>
          n/c
        </span>
        {metric.provenance.reason && <span className="muted reason">{metric.provenance.reason}</span>}
      </span>
    );
  }
  const badge =
    method === "approximate" ? (
      <span className="method-badge method-approximate" title="method: approximate">≈</span>
    ) : method === "sampled" ? (
      <span className="method-badge method-sampled" title="method: sampled">sampled</span>
    ) : null;
  const suffix = metric.denominator !== undefined ? ` / ${metric.denominator.toLocaleString()}` : "";
  return (
    <span className="metric-cell">
      {badge}
      <span className="mono">
        {formatValue(metric.value as Jsonish, unit)}
        {suffix}
      </span>
    </span>
  );
}

/**
 * ADR-015 provenance tag for a chart or observed value that is not exact: shows
 * "≈ approximate" / "sampled" / "not computed" so a figure is never read as a
 * precise count. Renders nothing for an ``exact`` (or absent) provenance, which
 * is the default and needs no noise.
 */
function MethodTag({ provenance }: { provenance?: Provenance | null }) {
  const method = provenance?.method;
  if (!method || method === "exact") return null;
  const cls = `method-badge method-${method}`;
  const label =
    method === "approximate" ? "≈ approximate" : method === "sampled" ? "sampled" : "not computed";
  return (
    <span className={cls} title={provenance?.reason ? `reason: ${provenance.reason}` : `method: ${method}`}>
      {label}
    </span>
  );
}

export default function Report() {
  const job = useStartedJob();
  const dataset = useSelectedDataset();
  const datasetId = job?.datasetId ?? dataset?.datasetId ?? null;
  const hasBridge = bridgeAvailable();

  const [summary, setSummary] = useState<ReportSummaryResult | null>(null);
  const [summaryStatus, setSummaryStatus] = useState<LoadStatus>("idle");
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [columns, setColumns] = useState<ColumnProfile[]>([]);
  const [colsStatus, setColsStatus] = useState<LoadStatus>("idle");
  const [colsError, setColsError] = useState<string | null>(null);
  const [colsTotal, setColsTotal] = useState(0);
  const [selectedCol, setSelectedCol] = useState<string | null>(null);

  // §3.4 Quality Assessment — paged, filterable quality.get_results set.
  const [checks, setChecks] = useState<QualityCheckResult[]>([]);
  const [checksTotal, setChecksTotal] = useState(0);
  const [checksStatus, setChecksStatus] = useState<LoadStatus>("idle");
  const [checksError, setChecksError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<"" | CheckStatus>("");
  const [dimensionFilter, setDimensionFilter] = useState<"" | QualityDimension>("");

  // §3.4/§4.2 Findings — paged, severity/category-filterable findings.get set.
  const [findings, setFindings] = useState<Finding[]>([]);
  const [findingsTotal, setFindingsTotal] = useState(0);
  const [findingsStatus, setFindingsStatus] = useState<LoadStatus>("idle");
  const [findingsError, setFindingsError] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState<"" | Severity>("");
  const [categoryFilter, setCategoryFilter] = useState<"" | QualityDimension>("");

  // §4.3 Recommendation bodies, fetched once for the run and keyed by id so each
  // finding can render exactly the recommendations it cites (recommendation_ids).
  const [recsById, setRecsById] = useState<Record<string, Recommendation>>({});
  const [recsStatus, setRecsStatus] = useState<LoadStatus>("idle");
  const [recsError, setRecsError] = useState<string | null>(null);

  // §3.5 Export — artifact.open ack state (no bytes cross; reveal opens the file).
  const [exportingKind, setExportingKind] = useState<string | null>(null);
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  // Fetch one page of column profiles, appending to what is already loaded.
  const loadColumns = useCallback(async (runId: string, offset: number) => {
    setColsStatus("loading");
    setColsError(null);
    try {
      const payload: ReportGetColumnsPayload = {
        run_id: runId,
        limit: COLUMN_PAGE_SIZE,
        offset,
      };
      const result = await bridgeSend<ReportGetColumnsPayload, ReportGetColumnsResult>(
        "report.get_columns",
        payload,
      );
      setColumns((prev) => (offset === 0 ? result.items : [...prev, ...result.items]));
      setColsTotal(result.page.total);
      setColsStatus("ready");
    } catch (e) {
      setColsStatus("error");
      setColsError(errText(e));
    }
  }, []);

  // Fetch one page of quality-check results, appending on "load more".
  const loadQuality = useCallback(
    async (runId: string, offset: number, status: "" | CheckStatus, dimension: "" | QualityDimension) => {
      setChecksStatus("loading");
      setChecksError(null);
      try {
        const payload: QualityGetResultsPayload = {
          run_id: runId,
          limit: QUALITY_PAGE_SIZE,
          offset,
        };
        if (status) payload.status = status;
        if (dimension) payload.dimension = dimension;
        const result = await bridgeSend<QualityGetResultsPayload, QualityGetResultsResult>(
          "quality.get_results",
          payload,
        );
        setChecks((prev) => (offset === 0 ? result.items : [...prev, ...result.items]));
        setChecksTotal(result.page.total);
        setChecksStatus("ready");
      } catch (e) {
        setChecksStatus("error");
        setChecksError(errText(e));
      }
    },
    [],
  );

  // Fetch one page of findings (§4.2), appending on "load more"; the severity/
  // category filters narrow server-side exactly like the quality table.
  const loadFindings = useCallback(
    async (
      runId: string,
      offset: number,
      severity: "" | Severity,
      category: "" | QualityDimension,
    ) => {
      setFindingsStatus("loading");
      setFindingsError(null);
      try {
        const payload: FindingsGetPayload = {
          run_id: runId,
          limit: FINDING_PAGE_SIZE,
          offset,
        };
        if (severity) payload.severity = severity;
        if (category) payload.category = category;
        const result = await bridgeSend<FindingsGetPayload, FindingsGetResult>(
          "findings.get",
          payload,
        );
        setFindings((prev) => (offset === 0 ? result.items : [...prev, ...result.items]));
        setFindingsTotal(result.page.total);
        setFindingsStatus("ready");
      } catch (e) {
        setFindingsStatus("error");
        setFindingsError(errText(e));
      }
    },
    [],
  );

  // Load the run's §4.3 recommendation bodies across pages into one id-keyed map
  // (recommendation rows are compact; looping bounded 50-row pages stays under the
  // §5.4 cap and lets any displayed finding resolve the recs it cites without a
  // per-finding round-trip).
  const loadRecommendations = useCallback(async (runId: string) => {
    setRecsStatus("loading");
    setRecsError(null);
    try {
      const acc: Record<string, Recommendation> = {};
      let offset = 0;
      let total = Infinity;
      while (offset < total) {
        const payload: RecommendationsGetPayload = {
          run_id: runId,
          limit: 50,
          offset,
        };
        const result = await bridgeSend<RecommendationsGetPayload, RecommendationsGetResult>(
          "recommendations.get",
          payload,
        );
        total = result.page.total;
        for (const rec of result.items) acc[rec.recommendation_id] = rec;
        offset += result.items.length;
        if (result.items.length === 0) break; // guard against a non-advancing page
      }
      setRecsById(acc);
      setRecsStatus("ready");
    } catch (e) {
      setRecsStatus("error");
      setRecsError(errText(e));
    }
  }, []);

  // §3.5 Export: artifact.open a run's recorded kind. Ack + reveal (no bytes
  // cross); the command can only reach a kind already in the run's result_refs,
  // so a missing artifact fails closed as a typed error, never a path leak.
  const exportArtifact = useCallback(
    async (runId: string, kind: string, label: string) => {
      setExportingKind(kind);
      setExportError(null);
      setExportMsg(null);
      try {
        const payload: ArtifactOpenPayload = { run_id: runId, kind, reveal: true };
        const result = await bridgeSend<ArtifactOpenPayload, ArtifactOpenResult>(
          "artifact.open",
          payload,
        );
        setExportMsg(`${label} opened (${result.size_bytes.toLocaleString()} bytes).`);
      } catch (e) {
        setExportError(`${label}: ${errText(e)}`);
      } finally {
        setExportingKind(null);
      }
    },
    [],
  );

  // Summary first (it resolves + supplies the run_id), then the first column page.
  useEffect(() => {
    if (!datasetId || !hasBridge) return;
    let cancelled = false;
    setSummaryStatus("loading");
    setSummaryError(null);
    setSummary(null);
    setColumns([]);
    setColsStatus("idle");
    setSelectedCol(null);
    (async () => {
      try {
        const payload: ReportSummaryPayload = { dataset_id: datasetId };
        const result = await bridgeSend<ReportSummaryPayload, ReportSummaryResult>(
          "report.get_summary",
          payload,
        );
        if (cancelled) return;
        setSummary(result);
        setSummaryStatus("ready");
        if (result.run_id) await loadColumns(result.run_id, 0);
      } catch (e) {
        if (cancelled) return;
        setSummaryStatus("error");
        setSummaryError(errText(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [datasetId, hasBridge, loadColumns]);

  const selected = useMemo(
    () => columns.find((c) => c.column === selectedCol) ?? null,
    [columns, selectedCol],
  );

  // Load/reload the quality-check page whenever the run or a filter changes
  // (the summary supplies run_id, so this runs after it resolves). Reset to the
  // first page on filter change — the §4.1 filters narrow server-side, not here.
  const runId = summary?.run_id;
  useEffect(() => {
    if (!runId || !hasBridge) return;
    setChecks([]);
    setChecksTotal(0);
    void loadQuality(runId, 0, statusFilter, dimensionFilter);
  }, [runId, hasBridge, statusFilter, dimensionFilter, loadQuality]);

  // Reload the findings page whenever the run or a §4.2 filter changes; reset to
  // the first page on filter change (severity/category narrow server-side).
  useEffect(() => {
    if (!runId || !hasBridge) return;
    setFindings([]);
    setFindingsTotal(0);
    void loadFindings(runId, 0, severityFilter, categoryFilter);
  }, [runId, hasBridge, severityFilter, categoryFilter, loadFindings]);

  // Fetch the run's recommendation bodies once per run — they decorate whichever
  // findings display (the same run_id the findings/summary resolve, ADR-020).
  useEffect(() => {
    if (!runId || !hasBridge) return;
    void loadRecommendations(runId);
  }, [runId, hasBridge, loadRecommendations]);

  // §3.3 dataset-level missing-rate bar, from the loaded column bodies only.
  const missingness = useMemo(() => missingnessChart(columns), [columns]);

  // --- No dataset chosen yet ------------------------------------------------
  if (!datasetId) {
    return (
      <section className="report">
        <h1>Report</h1>
        <div className="panel">
          <p className="hint">
            No dataset has been profiled in this session yet. Pick a dataset and run a
            profiling job first — the report reflects its latest successful run.
          </p>
          <div className="field-row">
            <Link className="btn" to="/">Select dataset</Link>
            <Link className="btn" to="/run">Configure a run</Link>
          </div>
        </div>
      </section>
    );
  }

  if (!hasBridge) {
    return (
      <section className="report">
        <h1>Report</h1>
        <div className="panel">
          <p className="mask-warn">The desktop bridge is not available — run inside the app shell.</p>
        </div>
      </section>
    );
  }

  const counts = summary?.check_status_counts ?? {};

  return (
    <section className="report">
      <h1>Data Profiling Overview</h1>

      {/* §3.1 Dataset Overview — counts-only, all from report.get_summary */}
      <div className="panel">
        {summaryStatus === "loading" && <p className="hint">Loading run summary…</p>}
        {summaryStatus === "error" && <p className="mask-warn">{summaryError}</p>}
        {summary && (
          <>
            <dl className="meta-grid">
              <dt>Dataset</dt>
              <dd className="mono">{summary.dataset_id}</dd>
              <dt>Profiling run</dt>
              <dd className="mono">{summary.run_id}</dd>
              <dt>Status</dt>
              <dd>
                <span className={`state-badge state-${summary.status ?? ""}`}>{summary.status}</span>
              </dd>
              <dt>Total rows</dt>
              <dd>
                {summary.row_count?.toLocaleString() ?? "—"}
                {summary.row_count_method && (
                  <span className="method-tag"> ({summary.row_count_method})</span>
                )}
              </dd>
              <dt>Total columns</dt>
              <dd>{summary.column_count ?? "—"}</dd>
              {summary.grain && (
                <>
                  <dt>Grain</dt>
                  <dd>{summary.grain}</dd>
                </>
              )}
              {summary.temporal_coverage && (
                <>
                  <dt>Temporal coverage</dt>
                  <dd className="mono">
                    {summary.temporal_coverage.column}: {summary.temporal_coverage.min ?? "—"} →{" "}
                    {summary.temporal_coverage.max ?? "—"}
                  </dd>
                </>
              )}
              {summary.sampling_enabled && (
                <>
                  <dt>Sampling</dt>
                  <dd className="mono">
                    seed {summary.sample_seed ?? "—"}
                  </dd>
                </>
              )}
              <dt>Connector / engine</dt>
              <dd className="mono">
                {summary.connector ?? "—"} · {summary.engine_version ?? "—"}
              </dd>
              {summary.duration_ms !== undefined && (
                <>
                  <dt>Duration</dt>
                  <dd>{(summary.duration_ms / 1000).toFixed(1)}s</dd>
                </>
              )}
            </dl>

            <div className="tally-row">
              <span className="tally tally-pass">PASS {counts.PASS ?? 0}</span>
              <span className="tally tally-fail">FAIL {counts.FAIL ?? 0}</span>
              <span className="tally tally-warn">WARN {counts.WARN ?? 0}</span>
              <span className="tally tally-error">ERROR {counts.ERROR ?? 0}</span>
            </div>
            <p className="hint">
              {summary.check_count ?? 0} checks evaluated · {summary.finding_count ?? 0} findings ·{" "}
              {summary.recommendation_count ?? 0} recommendations · {summary.error_count ?? 0} run errors.
              FAIL means the rule ran and the data violated it; ERROR means the check could not complete.
            </p>
          </>
        )}
      </div>

      {/* §3.2 Schema & Column Profiling — paged report.get_columns */}
      <h2>Schema &amp; Column Profiling</h2>
      <div className="panel">
        {colsStatus === "error" && <p className="mask-warn">{colsError}</p>}
        {colsStatus !== "error" && columns.length === 0 && (
          <p className="hint">
            {colsStatus === "loading" ? "Loading column profiles…" : "No columns returned."}
          </p>
        )}
        {columns.length > 0 && (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Logical type</th>
                    <th>Semantic</th>
                    <th>Missing</th>
                    <th>Distinct</th>
                    <th>Unit</th>
                  </tr>
                </thead>
                <tbody>
                  {columns.map((c) => (
                    <tr
                      key={c.column}
                      className={c.column === selectedCol ? "row-selected" : undefined}
                      onClick={() => setSelectedCol(c.column)}
                    >
                      <td className="mono">{c.column}</td>
                      <td>{c.logical_type}</td>
                      <td>{c.semantic_type}</td>
                      <td>
                        {c.missingness ? <MetricCell metric={c.missingness.missing_rate} /> : "—"}
                      </td>
                      <td>
                        {c.uniqueness ? <MetricCell metric={c.uniqueness.distinct_count} /> : "—"}
                      </td>
                      <td>{c.unit ? `${c.unit.currency ?? c.unit.status}` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <span className="muted">
                {columns.length} of {colsTotal} columns
              </span>
              {columns.length < colsTotal && (
                <button
                  className="btn btn-sm"
                  disabled={colsStatus === "loading"}
                  onClick={() => summary?.run_id && loadColumns(summary.run_id, columns.length)}
                >
                  Load more
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {/* Per-column detail (§3.2.1 / §3.2.2) for the selected column */}
      {selected && <ColumnDetail column={selected} />}

      {/* §3.3 Visualizations — dataset-level missing-rate bar (from column bodies) */}
      <h2>Visualizations</h2>
      <div className="panel">
        {missingness ? (
          <ChartFigure spec={missingness} />
        ) : (
          <p className="hint">
            {colsStatus === "loading"
              ? "Preparing charts…"
              : "No missing-rate values were computed for the loaded columns."}
          </p>
        )}
        <p className="hint">
          Charts are built from the stored §3.2 metrics and rendered client-side
          (histogram, categorical frequency, missing-rate, IQR spread) — select a
          column above to see its per-column distribution charts.
        </p>
      </div>

      {/* §3.4 Data Quality Assessment — paged quality.get_results, FAIL≠ERROR (§4.1) */}
      <h2>Data Quality Assessment</h2>
      <div className="panel">
        <div className="field-row">
          <span className="field-label">Status</span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as "" | CheckStatus)}
          >
            <option value="">all</option>
            {CheckStatusValues.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="field-label">Dimension</span>
          <select
            value={dimensionFilter}
            onChange={(e) => setDimensionFilter(e.target.value as "" | QualityDimension)}
          >
            <option value="">all</option>
            {QualityDimensionValues.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>

        {checksError && <p className="mask-warn">{checksError}</p>}
        {checks.length === 0 && !checksError && (
          <p className="hint">
            {checksStatus === "loading"
              ? "Loading quality checks…"
              : "No quality checks recorded for this run."}
          </p>
        )}
        {checks.length > 0 && (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Rule</th>
                    <th>Dimension</th>
                    <th>Status</th>
                    <th>Column</th>
                    <th>Observed</th>
                    <th>Threshold</th>
                    <th>Failing</th>
                  </tr>
                </thead>
                <tbody>
                  {checks.map((c) => (
                    <tr key={`${c.rule_id}@@${c.rule_version}`}>
                      <td className="mono">{c.rule_id}</td>
                      <td>{c.dimension}</td>
                      <td>
                        <span className={`q-status q-${c.status}`}>{c.status}</span>
                      </td>
                      <td className="mono">{c.column ?? "—"}</td>
                      <td>
                        {c.status === "ERROR" ? (
                          <span className="muted" title={c.error?.message ?? ""}>
                            {c.error?.code ?? "error"}
                          </span>
                        ) : (
                          <MetricCell metric={c.observed} />
                        )}
                      </td>
                      <td className="mono">
                        {c.status === "ERROR" || !c.threshold
                          ? "—"
                          : `${c.threshold.operator} ${formatValue(c.threshold.value as Jsonish)}`}
                      </td>
                      <td className="mono">
                        {c.failing_record_count != null
                          ? c.failing_record_count.toLocaleString()
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <span className="muted">
                {checks.length} of {checksTotal} checks
              </span>
              {checks.length < checksTotal && (
                <button
                  className="btn btn-sm"
                  disabled={checksStatus === "loading"}
                  onClick={() =>
                    runId && loadQuality(runId, checks.length, statusFilter, dimensionFilter)
                  }
                >
                  Load more
                </button>
              )}
            </div>
          </>
        )}
        <p className="hint">
          FAIL means the rule ran and the data violated it; ERROR means the check could
          not complete — its observed value is <em>not</em> evidence the rule holds (§4.1).
          Scorecard counts above come from {summary?.check_count ?? 0} checks.
        </p>
      </div>

      {/* §3.4/§4.2 Findings & Recommendations — paged findings.get, §4.3 bodies via recommendations.get (ADR-020) */}
      <h2>Findings &amp; Recommendations</h2>
      <div className="panel">
        <div className="field-row">
          <span className="field-label">Severity</span>
          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value as "" | Severity)}
          >
            <option value="">all</option>
            {SeverityValues.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="field-label">Category</span>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value as "" | QualityDimension)}
          >
            <option value="">all</option>
            {QualityDimensionValues.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>

        {findingsError && <p className="mask-warn">{findingsError}</p>}
        {recsError && <p className="mask-warn">Recommendations: {recsError}</p>}
        {findings.length === 0 && !findingsError && (
          <p className="hint">
            {findingsStatus === "loading"
              ? "Loading findings…"
              : "No findings recorded for this run."}
          </p>
        )}
        {findings.map((f) => (
          <FindingCard
            key={f.finding_id}
            finding={f}
            recsById={recsById}
            recsLoading={recsStatus === "loading"}
          />
        ))}
        {findings.length > 0 && (
          <div className="pager">
            <span className="muted">
              {findings.length} of {findingsTotal} findings
            </span>
            {findings.length < findingsTotal && (
              <button
                className="btn btn-sm"
                disabled={findingsStatus === "loading"}
                onClick={() =>
                  runId && loadFindings(runId, findings.length, severityFilter, categoryFilter)
                }
              >
                Load more
              </button>
            )}
          </div>
        )}
        <p className="hint">
          Each Finding keeps facts / interpretation / hypotheses structurally separate
          (§7): facts are computed evidence (with provenance), a hypothesis is an
          unverified suspected cause, and a recommendation is a suggested action citing
          evidence — never folded into a fact. The <span className="mono">fingerprint</span>{" "}
          is deterministic across runs (ADR-016).
        </p>
      </div>

      {/* §3.5 Export — artifact.open ack + reveal (no bytes cross) */}
      <h2>Export</h2>
      <div className="panel">
        {exportError && <p className="mask-warn">{exportError}</p>}
        {exportMsg && <p className="hint">{exportMsg}</p>}
        <div className="export-row">
          {EXPORT_KINDS.map(({ kind, label, hint }) => {
            const available =
              !summary?.result_refs || Object.prototype.hasOwnProperty.call(summary.result_refs, kind);
            return (
              <button
                key={kind}
                className="btn"
                disabled={!runId || !available || exportingKind !== null}
                title={available ? hint : "Not produced by this run"}
                onClick={() => runId && void exportArtifact(runId, kind, label)}
              >
                {exportingKind === kind ? "Opening…" : label}
                {!available && " (unavailable)"}
              </button>
            );
          })}
        </div>
        <p className="hint">
          Opens the run&apos;s report / full JSON result / issue CSV through the OS viewer.
          No file bytes cross the bridge — the command only acks + reveals an artifact
          already recorded for the run (SECURITY §3).
        </p>
      </div>
    </section>
  );
}

// --- §3.2.1 / §3.2.2 selected-column detail ----------------------------------

function ColumnDetail({ column }: { column: ColumnProfile }) {
  const unitLabel = column.unit
    ? column.unit.currency
      ? `${column.unit.currency} (${column.unit.status})`
      : column.unit.status
    : null;

  const charts = chartsForColumn(column);

  return (
    <div className="panel column-detail">
      <div className="detail-head">
        <h2 className="mono">{column.column}</h2>
        <span className="muted">
          #{column.ordinal} · physical <span className="mono">{column.physical_type}</span> ·{" "}
          {column.logical_type} · {column.semantic_type}
        </span>
      </div>
      {unitLabel && (
        <p className="hint">
          Unit: <span className="mono">{unitLabel}</span>
          {column.unit?.status !== "known" && " — not confirmed by config (never asserted)."}
        </p>
      )}

      {column.missingness && (
        <div className="detail-block">
          <h3>Missingness</h3>
          <dl className="meta-grid">
            <dt>Null count</dt>
            <dd className="mono">{column.missingness.null_count.toLocaleString()}</dd>
            <dt>Blank count</dt>
            <dd className="mono">{column.missingness.blank_count.toLocaleString()}</dd>
            <dt>Sentinel count</dt>
            <dd className="mono">{column.missingness.sentinel_count.toLocaleString()}</dd>
            <dt>Missing rate</dt>
            <dd>
              <MetricCell metric={column.missingness.missing_rate} />
            </dd>
          </dl>
        </div>
      )}

      {column.uniqueness && (
        <div className="detail-block">
          <h3>Uniqueness</h3>
          <dl className="meta-grid">
            <dt>Distinct count</dt>
            <dd>
              <MetricCell metric={column.uniqueness.distinct_count} />
            </dd>
            <dt>Unique candidate</dt>
            <dd>{column.uniqueness.is_unique_candidate ? "yes" : "no"}</dd>
          </dl>
        </div>
      )}

      {column.stats && (
        <div className="detail-block">
          <h3>Descriptive statistics</h3>
          <dl className="meta-grid">
            {column.stats.valid_count !== undefined && (
              <>
                <dt>Valid count</dt>
                <dd className="mono">{column.stats.valid_count.toLocaleString()}</dd>
              </>
            )}
            <dt>Min</dt>
            <dd><MetricCell metric={column.stats.min} /></dd>
            <dt>Max</dt>
            <dd><MetricCell metric={column.stats.max} /></dd>
            <dt>Mean</dt>
            <dd><MetricCell metric={column.stats.mean} /></dd>
            <dt>Median</dt>
            <dd><MetricCell metric={column.stats.median} /></dd>
            {column.stats.p25 && (
              <>
                <dt>P25</dt>
                <dd><MetricCell metric={column.stats.p25} /></dd>
              </>
            )}
            {column.stats.p75 && (
              <>
                <dt>P75</dt>
                <dd><MetricCell metric={column.stats.p75} /></dd>
              </>
            )}
            {column.stats.p95 && (
              <>
                <dt>P95</dt>
                <dd><MetricCell metric={column.stats.p95} /></dd>
              </>
            )}
            {column.stats.p99 && (
              <>
                <dt>P99</dt>
                <dd><MetricCell metric={column.stats.p99} /></dd>
              </>
            )}
            {column.stats.stddev && (
              <>
                <dt>Stddev</dt>
                <dd><MetricCell metric={column.stats.stddev} /></dd>
              </>
            )}
          </dl>
        </div>
      )}

      {column.distribution && (
        <div className="detail-block">
          <h3>Distribution</h3>
          <dl className="meta-grid">
            {column.distribution.skewness && (
              <>
                <dt>Skewness</dt>
                <dd><MetricCell metric={column.distribution.skewness} /></dd>
              </>
            )}
            {column.distribution.outlier_candidates && (
              <>
                <dt>Outlier candidates</dt>
                <dd className="mono">
                  {column.distribution.outlier_candidates.count.toLocaleString()}{" "}
                  <span className="method-tag">({column.distribution.outlier_candidates.method})</span>
                </dd>
              </>
            )}
            {column.distribution.histogram && (
              <>
                <dt>Histogram</dt>
                <dd className="mono">
                  {column.distribution.histogram.bins.length} bins (chart below)
                </dd>
              </>
            )}
          </dl>
        </div>
      )}

      {column.categorical && (
        <div className="detail-block">
          <h3>Categorical</h3>
          <p className="hint">
            {column.categorical.categories?.length ?? 0} categories
            {column.categorical.rare_categories?.length
              ? ` · ${column.categorical.rare_categories.length} rare`
              : ""}
            {column.categorical.label_inconsistencies?.length
              ? ` · ${column.categorical.label_inconsistencies.length} label inconsistencies`
              : ""}
          </p>
          {column.categorical.categories && column.categorical.categories.length > 0 && (
            <ul className="cat-list mono">
              {column.categorical.categories.slice(0, 20).map((cat, i) => (
                <li key={cat}>
                  {cat}
                  {column.categorical?.frequencies?.[i] !== undefined
                    ? ` — ${column.categorical.frequencies[i].toLocaleString()}`
                    : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {column.temporal && (
        <div className="detail-block">
          <h3>Temporal</h3>
          <dl className="meta-grid">
            {column.temporal.range && (
              <>
                <dt>Range</dt>
                <dd className="mono">
                  {column.temporal.range.min ?? "—"} → {column.temporal.range.max ?? "—"}
                </dd>
              </>
            )}
            {column.temporal.timezone_source && (
              <>
                <dt>Timezone source</dt>
                <dd className="mono">{column.temporal.timezone_source}</dd>
              </>
            )}
            {column.temporal.gaps && (
              <>
                <dt>Gaps</dt>
                <dd className="mono">
                  {Array.isArray(column.temporal.gaps)
                    ? `${column.temporal.gaps.length} gap(s)`
                    : "recorded"}
                </dd>
              </>
            )}
          </dl>
        </div>
      )}

      {charts.length > 0 && (
        <div className="detail-block">
          <h3>Visualizations</h3>
          {charts.map((spec) => (
            <ChartFigure key={`${column.column}-${spec.kind}`} spec={spec} />
          ))}
        </div>
      )}
    </div>
  );
}

// --- §3.3 chart figure with an ADR-015 provenance caption --------------------

function ChartFigure({ spec }: { spec: PlotlySpec }) {
  return (
    <figure className="chart-figure">
      <PlotlyChart spec={spec} />
      <figcaption>
        <MethodTag provenance={spec.provenance} />
        {spec.provenance?.reason && <span className="muted reason">{spec.provenance.reason}</span>}
      </figcaption>
    </figure>
  );
}

// --- §4.2 Finding + §4.3 Recommendation cards (epistemic separation kept) -----

/**
 * One §4.2 Finding. facts / interpretation / hypotheses render in their own
 * blocks and a recommendation renders as a cited §4.3 body — never folded into a
 * fact (CONTRACTS §7). The body of each cited recommendation resolves against the
 * run's {@link recommendations.get} map (ADR-020); a cited id with no loaded body
 * is shown as a reference, never invented.
 */
function FindingCard({
  finding,
  recsById,
  recsLoading,
}: {
  finding: Finding;
  recsById: Record<string, Recommendation>;
  recsLoading: boolean;
}) {
  const cited = finding.recommendation_ids ?? [];
  const resolved = cited
    .map((id) => recsById[id])
    .filter((r): r is Recommendation => Boolean(r));
  const unresolved = cited.filter((id) => !recsById[id]);
  const scope = finding.scope ?? {};
  const affected = scope.affected_records;

  return (
    <article className="finding-card">
      <header className="finding-head">
        <span className="mono finding-id">{finding.finding_id}</span>
        <h3 className="finding-title">{finding.title}</h3>
        <span className={`sev-pill sev-${finding.severity}`}>{finding.severity}</span>
        <span className="cat-pill">{finding.category}</span>
        <span className="muted conf">confidence: {finding.confidence}</span>
      </header>
      <p className="finding-summary">{finding.summary}</p>
      <p className="mono muted fingerprint" title="deterministic across runs (ADR-016)">
        fingerprint: {finding.fingerprint}
      </p>
      {(scope.column || affected != null) && (
        <p className="hint finding-scope">
          {scope.column && (
            <>
              column <span className="mono">{scope.column}</span>
            </>
          )}
          {scope.column && affected != null && " · "}
          {affected != null && <>{affected.toLocaleString()} affected records</>}
        </p>
      )}

      {finding.facts.length > 0 && (
        <div className="detail-block">
          <h4>Facts (evidence)</h4>
          <ul className="fact-list">
            {finding.facts.map((fact, i) => (
              <li key={`${fact.metric_ref}-${i}`} className="fact-item">
                <span className="mono">{fact.metric_ref}</span>{" "}
                <span className="metric-cell">
                  <MethodTag provenance={fact.provenance} />
                  <span className="mono">{formatValue(fact.value as Jsonish)}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {finding.interpretation && (
        <div className="detail-block">
          <h4>Interpretation</h4>
          <p>{finding.interpretation}</p>
        </div>
      )}

      {finding.hypotheses.length > 0 && (
        <div className="detail-block">
          <h4>Hypotheses (unverified)</h4>
          <ul className="hypo-list">
            {finding.hypotheses.map((h, i) => (
              <li key={i}>
                <span className={`hypo-status hypo-${h.status}`}>{h.status}</span> {h.text}
                {h.suggested_probe && <span className="muted"> — probe: {h.suggested_probe}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="detail-block recommendations">
        <h4>Recommendations</h4>
        {resolved.map((rec) => (
          <RecommendationBlock key={rec.recommendation_id} rec={rec} />
        ))}
        {resolved.length === 0 && recsLoading && (
          <p className="hint">Loading recommendations…</p>
        )}
        {resolved.length === 0 && !recsLoading && cited.length === 0 && (
          <p className="muted">No recommendation recorded for this finding.</p>
        )}
        {unresolved.length > 0 && (
          <p className="hint">
            Also cites <span className="mono">{unresolved.join(", ")}</span> — body not
            available for this run.
          </p>
        )}
      </div>
    </article>
  );
}

function RecommendationBlock({ rec }: { rec: Recommendation }) {
  return (
    <div className="rec-block">
      <div className="rec-head">
        <span className="mono rec-id">{rec.recommendation_id}</span>
        <span className={`reason-pill reason-${rec.reason_type}`}>{rec.reason_type}</span>
        <span className="muted">{rec.status}</span>
      </div>
      <p className="rec-action">{rec.action}</p>
      <p className="muted rec-rationale">{rec.rationale}</p>
      {rec.target_improvement && <p className="rec-target">Target: {rec.target_improvement}</p>}
      {rec.do_not && rec.do_not.length > 0 && (
        <ul className="rec-donot">
          {rec.do_not.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      )}
      {rec.evidence_refs.length > 0 && (
        <p className="mono muted rec-evidence">evidence: {rec.evidence_refs.join(", ")}</p>
      )}
    </div>
  );
}
