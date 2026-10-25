/**
 * M6-T5 — Plotly chart construction for the Report View (§3.3), built ONLY
 * from the stored §3.2 ColumnProfile metric bodies already delivered by
 * ``report.get_columns`` — no metric is recomputed here (§10 first bullet) and
 * no new bridge command is involved.
 *
 * Parity with the HTML report: the shapes below mirror
 * ``data_profiling_engine/reporting/html_report.py`` (``_charts_for_column`` /
 * ``_box_spec`` / ``_missingness_chart``) so the WebView report and the
 * standalone HTML report render the same data-type-aware charts from the same
 * numbers — histogram for a numeric column that computed one, an IQR spread box
 * from the stored quantiles, a frequency bar for a categorical column, a volume
 * bar for a temporal column, and one dataset-level missing-rate bar.
 *
 * Selection is data-type-aware by design (§10 last bullet): a column yields the
 * charts its type actually supports — nothing is fabricated to fill a slot.
 * Provenance rides along so the caller can badge a non-exact chart (ADR-015).
 */
import type {
  ColumnProfile,
  MetricValue,
  Provenance,
} from "../types/contracts";

// The vendored Plotly bundle is served from Vite's ``publicDir`` (pointed at
// ``reporting/assets`` in vite.config.ts) and lands at the dist root, so it is
// reachable with a DOCUMENT-relative URL. A <script src="plotly.min.js"> resolves
// against the page base (``file://…/dist/index.html`` in production, ``/`` in
// dev), so the same string works offline under file:// — no CDN tag, no npm
// dependency, no network (ADR-005/009/010), the SAME file the HTML report inlines.
const PLOTLY_SRC = "plotly.min.js";

/** One Plotly figure: the trace array + layout, plus the metric provenance. */
export interface PlotlySpec {
  kind: string;
  data: unknown[];
  layout: Record<string, unknown>;
  provenance?: Provenance | null;
}

// --- lazy global loader ------------------------------------------------------

let plotlyLoader: Promise<PlotlyStatic> | null = null;

/**
 * Inject the vendored Plotly script once and resolve ``window.Plotly``. Safe to
 * call repeatedly (the load promise is memoised); a second script tag is never
 * added if the global is already present.
 */
export function loadPlotly(): Promise<PlotlyStatic> {
  if (typeof window !== "undefined" && window.Plotly) {
    return Promise.resolve(window.Plotly);
  }
  if (!plotlyLoader) {
    plotlyLoader = new Promise<PlotlyStatic>((resolve, reject) => {
      const el = document.createElement("script");
      el.src = PLOTLY_SRC;
      el.async = true;
      el.onload = () => {
        if (window.Plotly) resolve(window.Plotly);
        else reject(new Error("Plotly loaded but window.Plotly is missing"));
      };
      el.onerror = () => {
        plotlyLoader = null;
        reject(new Error("Failed to load the vendored Plotly bundle."));
      };
      document.head.appendChild(el);
    });
  }
  return plotlyLoader;
}

// --- helpers -----------------------------------------------------------------

/** Numeric leaf of a MetricValue, or null when absent / not_computed. */
function num(mv?: MetricValue | null): number | null {
  if (!mv || typeof mv.value !== "number") return null;
  return mv.value;
}

// --- per-column charts (§3.3) ------------------------------------------------

/**
 * §10 "outlier box": an IQR spread drawn entirely from the stored quantile
 * MetricValues (min/p25/median/p75/max). Returns null unless all five exist —
 * an absent value is never silently substituted (AGENTS §3).
 */
function boxSpec(col: ColumnProfile): PlotlySpec | null {
  const name = col.column;
  const stats = col.stats;
  if (!stats) return null;
  const q1 = num(stats.p25);
  const q3 = num(stats.p75);
  const med = num(stats.median);
  const lo = num(stats.min);
  const hi = num(stats.max);
  if (q1 === null || q3 === null || med === null || lo === null || hi === null) {
    return null;
  }
  const trace = {
    type: "box",
    name,
    orientation: "h",
    q1: [q1],
    median: [med],
    q3: [q3],
    lowerfence: [lo],
    upperfence: [hi],
    marker: { color: "#4a7deb" },
    hoverinfo: "x",
  };
  const outl = col.distribution?.outlier_candidates?.count;
  const layout = {
    title: `${name} — spread (IQR box)` + (outl ? ` · ${outl} outlier candidates` : ""),
    xaxis: { title: "value" },
    margin: { l: 56, r: 16, t: 48, b: 40 },
    height: 220,
  };
  return { kind: "box", data: [trace], layout, provenance: stats.p25?.provenance };
}

/** Histogram bar for a numeric column that computed one (left-edge bins). */
function histogramSpec(col: ColumnProfile): PlotlySpec | null {
  const hist = col.distribution?.histogram;
  if (!hist || !hist.bins?.length || !hist.counts?.length) return null;
  const numericBins = hist.bins.every((b) => typeof b === "number");
  if (!numericBins) return null;
  const edges = hist.bins as number[];
  const step = edges.length > 1 ? edges[1] - edges[0] : 0;
  const centers = edges.map((e) => e + step / 2);
  const n = hist.counts.length;
  const trace = {
    x: centers.slice(0, n),
    y: hist.counts,
    type: "bar",
    marker: { color: "#4a7deb" },
    hovertemplate: "bin edge %{customdata:.6g}<br>count %{y}<extra></extra>",
    customdata: edges.slice(0, n),
  };
  const layout = {
    title: `${col.column} — distribution (histogram)`,
    xaxis: { title: "value" },
    yaxis: { title: "count" },
    bargap: 0.02,
    margin: { l: 56, r: 16, t: 48, b: 48 },
  };
  return { kind: "histogram", data: [trace], layout, provenance: hist.provenance };
}

/** Frequency bar for a categorical column (top categories). */
function categoricalSpec(col: ColumnProfile): PlotlySpec | null {
  const cats = col.categorical?.categories;
  const freqs = col.categorical?.frequencies;
  if (!cats || !freqs || cats.length === 0 || cats.length !== freqs.length) {
    return null;
  }
  const trace = {
    x: cats,
    y: freqs,
    type: "bar",
    marker: { color: "#2fa385" },
    hovertemplate: "%{x}<br>count %{y}<extra></extra>",
  };
  const layout = {
    title: `${col.column} — top categories`,
    xaxis: { title: "category", tickangle: -30 },
    yaxis: { title: "count" },
    margin: { l: 56, r: 16, t: 48, b: 72 },
  };
  return { kind: "bar", data: [trace], layout, provenance: null };
}

/** Volume bar per temporal granularity bucket. */
function temporalSpec(col: ColumnProfile): PlotlySpec | null {
  const volume = col.temporal?.volume_per_granularity as
    | { buckets?: Array<{ period?: string; count?: number }>; granularity?: string }
    | null
    | undefined;
  const buckets = volume?.buckets;
  if (!Array.isArray(buckets) || buckets.length === 0) return null;
  const periods = buckets.map((b) => String(b.period ?? ""));
  const counts = buckets.map((b) => b.count ?? 0);
  const trace = {
    x: periods,
    y: counts,
    type: "bar",
    marker: { color: "#8b5fc7" },
    hovertemplate: "%{x}<br>rows %{y}<extra></extra>",
  };
  const layout = {
    title: `${col.column} — volume per ${volume?.granularity ?? "bucket"}`,
    xaxis: { title: "period" },
    yaxis: { title: "row count" },
    margin: { l: 56, r: 16, t: 48, b: 56 },
  };
  return { kind: "volume", data: [trace], layout, provenance: null };
}

/**
 * The data-type-aware chart list for one column, mirroring HTML report
 * ``_charts_for_column``: numeric-with-histogram → [histogram, box]; numeric
 * without histogram but with stats → [box]; categorical → [frequency bar];
 * temporal → [volume bar]; otherwise no chart.
 */
export function chartsForColumn(col: ColumnProfile): PlotlySpec[] {
  const hist = histogramSpec(col);
  if (hist) {
    const box = boxSpec(col);
    return box ? [hist, box] : [hist];
  }
  if (col.stats) {
    const box = boxSpec(col);
    if (box) return [box];
  }
  const cat = categoricalSpec(col);
  if (cat) return [cat];
  const temporal = temporalSpec(col);
  if (temporal) return [temporal];
  return [];
}

// --- dataset-level missingness bar (§3.3, "Missing rate per column") ---------

/**
 * One bar per column's stored missing_rate — the §3.3 "Missing rate per kolom"
 * view. Values are the engine's own MetricValues; a column whose missing rate
 * was not computed is skipped (absent ≠ 0), and the method is carried in the
 * hover text so a non-exact rate is not read as exact (ADR-015).
 */
export function missingnessChart(cols: ColumnProfile[]): PlotlySpec | null {
  const names: string[] = [];
  const rates: number[] = [];
  const methods: string[] = [];
  for (const c of cols) {
    const mv = c.missingness?.missing_rate;
    const v = num(mv);
    if (v === null) continue;
    names.push(c.column);
    rates.push(v);
    methods.push(mv?.provenance?.method ?? "exact");
  }
  if (names.length === 0) return null;
  const trace = {
    x: names,
    y: rates,
    type: "bar",
    marker: { color: "#d9822b" },
    text: methods,
    hovertemplate: "%{x}<br>missing %{y:.3%}<br>method %{text}<extra></extra>",
  };
  const layout = {
    title: "Missing rate per column",
    yaxis: { title: "missing rate", tickformat: ".1%" },
    margin: { l: 64, r: 16, t: 48, b: 88 },
  };
  return { kind: "missingness", data: [trace], layout, provenance: null };
}
