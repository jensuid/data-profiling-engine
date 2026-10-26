/**
 * M6-T1 — Dataset Selection + Inspect/Preview screen.
 *
 * Drives the two §5.2 read commands `dataset.inspect` and `dataset.preview`
 * through the typed bridge client (api.ts). MVP connectors are CSV + Parquet
 * (ADR-011); no HTTP (ADR-005); heavy work is in DuckDB in-process, never the
 * UI thread beyond one bounded ≤200-row page (§5.4). PII masking is on by
 * default (SECURITY §4) — the UI labels the opt-out explicitly.
 *
 * The screen does NOT start a profiling job (that is M6-T2/M6-T3); it shows
 * schema, metadata, and a sample preview so the user can confirm the right
 * file before proceeding.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { BridgeError, bridgeSelectSourceFile } from "../bridge/api";
import { useBridgeReady } from "../bridge/useBridgeReady";
import { setSelectedDataset } from "../state/selection";
import type {
  DatasetInspectResult,
  DatasetInspectPayload,
  DatasetPreviewResult,
  DatasetPreviewPayload,
} from "../types/contracts";
import { bridgeSend } from "../bridge/api";

// --- UI state helpers ---

type LoadStatus = "idle" | "loading" | "ready" | "error";

interface PreviewState {
  data: DatasetPreviewResult | null;
  status: LoadStatus;
  error: string | null;
}

// Preview page-size: §5.4 caps at 200; UI default is 50 (fits screen).
const PAGE_SIZE = 50;

// --- Component ---

export default function DatasetSelection() {
  const [sourcePath, setSourcePath] = useState("");
  const [inspect, setInspect] = useState<DatasetInspectResult | null>(null);
  const [inspectStatus, setInspectStatus] = useState<LoadStatus>("idle");
  const [inspectError, setInspectError] = useState<string | null>(null);

  const [preview, setPreview] = useState<PreviewState>({
    data: null,
    status: "idle",
    error: null,
  });
  const [maskPii, setMaskPii] = useState(true);
  const [offset, setOffset] = useState(0);
  const [browsing, setBrowsing] = useState(false);
  // GOTCHA (pywebview 6.2.1 / Cocoa): cancelling the native open dialog with
  // Escape can leave the dialog.open_file promise unsettled, latching the
  // button on "Opening…" forever. Bounded watchdog resets the flag; a late
  // settle from the abandoned dialog is ignored via this epoch counter.
  const browseEpoch = useRef(0);

  // Bridge availability check — outside the desktop shell the commands fail
  // gracefully; we show a banner rather than crashing.
  const hasBridge = useBridgeReady();

  // --- handlers ---

  const doInspect = useCallback(async () => {
    if (!sourcePath.trim()) return;
    setInspectStatus("loading");
    setInspectError(null);
    setInspect(null);
    setPreview({ data: null, status: "idle", error: null });
    setOffset(0);
    try {
      const payload: DatasetInspectPayload = { source_path: sourcePath.trim() };
      const result = await bridgeSend<DatasetInspectPayload, DatasetInspectResult>(
        "dataset.inspect",
        payload,
      );
      setInspect(result);
      setInspectStatus("ready");
      // M6-T2: hand the confirmed dataset to the Run Configuration screen
      // through the shared selection store (survives HashRouter navigation).
      setSelectedDataset({
        sourcePath: sourcePath.trim(),
        datasetId: result.dataset_id,
        columns: result.columns.map((c) => c.name),
      });
    } catch (e) {
      setInspectStatus("error");
      setInspectError(e instanceof BridgeError ? `${e.code}: ${e.message}` : String(e));
    }
  }, [sourcePath]);

  const doPreview = useCallback(
    async (pageOffset: number) => {
      if (!sourcePath.trim()) return;
      setPreview((s) => ({ ...s, status: "loading", error: null }));
      try {
        const payload: DatasetPreviewPayload = {
          source_path: sourcePath.trim(),
          limit: PAGE_SIZE,
          offset: pageOffset,
          mask_pii: maskPii,
        };
        const result = await bridgeSend<DatasetPreviewPayload, DatasetPreviewResult>(
          "dataset.preview",
          payload,
        );
        setPreview({ data: result, status: "ready", error: null });
      } catch (e) {
        setPreview({
          data: null,
          status: "error",
          error: e instanceof BridgeError ? `${e.code}: ${e.message}` : String(e),
        });
      }
    },
    [sourcePath, maskPii],
  );

  // Auto-preview once inspect lands (the file is confirmed readable).
  useEffect(() => {
    if (inspect && inspectStatus === "ready") {
      doPreview(offset);
    }
  }, [inspect, inspectStatus, offset, doPreview]);

  // ADR-022: native open dialog through the bridge — the pick only fills the
  // path field; inspect/preview then run the ordinary allow-listed reads.
  const handleBrowse = useCallback(async () => {
    const epoch = ++browseEpoch.current;
    setBrowsing(true);
    // Cancel-hang watchdog (see browseEpoch comment): if the host never
    // answers, unlock the button after 15 s. A genuine pick that lands after
    // the watchdog still fills the path — harmless, the user just chose it.
    const watchdog = window.setTimeout(
      () => epoch === browseEpoch.current && setBrowsing(false),
      15_000,
    );
    try {
      const path = await bridgeSelectSourceFile();
      if (epoch !== browseEpoch.current) return;
      if (path) setSourcePath(path);
    } catch (e) {
      if (epoch !== browseEpoch.current) return;
      setInspectStatus("error");
      setInspectError(e instanceof BridgeError ? `${e.code}: ${e.message}` : String(e));
    } finally {
      window.clearTimeout(watchdog);
      if (epoch === browseEpoch.current) setBrowsing(false);
    }
  }, []);

  // Keyboard: Enter in the path field triggers inspect.
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") doInspect();
  };

  // --- render ---

  return (
    <section className="dataset-selection">
      <h1>Dataset Selection</h1>
      {!hasBridge && (
        <div className="banner-warn">
          Desktop bridge not detected — run inside the packaged app shell
          (pywebview + DuckDB worker). Read commands cannot execute.
        </div>
      )}

      {/* Path input */}
      <div className="path-row">
        <input
          type="text"
          className="path-input"
          placeholder="Absolute path to a CSV or Parquet file…"
          value={sourcePath}
          onChange={(e) => setSourcePath(e.target.value)}
          onKeyDown={handleKeyDown}
          spellCheck={false}
        />
        <button
          className="btn"
          onClick={handleBrowse}
          disabled={!hasBridge || browsing}
          title="Pick a CSV or Parquet file with the native open dialog"
        >
          {browsing ? "Opening…" : "Browse…"}
        </button>
        <button
          className="btn"
          onClick={doInspect}
          disabled={!sourcePath.trim() || inspectStatus === "loading"}
        >
          {inspectStatus === "loading" ? "Inspecting…" : "Inspect"}
        </button>
      </div>

      {/* Inspect error */}
      {inspectStatus === "error" && inspectError && (
        <div className="error-box">{inspectError}</div>
      )}

      {/* Inspect result: metadata + column table */}
      {inspect && (
        <div className="inspect-panel">
          <h2>Schema & Metadata</h2>
          <dl className="meta-grid">
            <dt>File</dt>
            <dd>{inspect.source_ref}</dd>
            <dt>Connector</dt>
            <dd>{inspect.connector}</dd>
            <dt>Dataset ID</dt>
            <dd>{inspect.dataset_id}</dd>
            <dt>Size</dt>
            <dd>{formatBytes(inspect.size_bytes)}</dd>
            <dt>Modified</dt>
            <dd>{inspect.modified_at ?? "—"}</dd>
            <dt>Content Hash</dt>
            <dd className="mono">{inspect.content_hash ?? "—"}</dd>
            <dt>Columns</dt>
            <dd>{inspect.column_count}</dd>
            {inspect.row_count != null && (
              <>
                <dt>Rows</dt>
                <dd>
                  {inspect.row_count.toLocaleString()}
                  {inspect.row_count_method && (
                    <span className="method-tag"> ({inspect.row_count_method})</span>
                  )}
                </dd>
              </>
            )}
          </dl>

          {inspect.last_run && (
            <div className="last-run-badge">
              Previously profiled: run{" "}
              <code>{String(inspect.last_run.run_id ?? "")}</code> —{" "}
              {String(inspect.last_run.status ?? "unknown")}
            </div>
          )}

          <h3>Columns</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>Physical Type</th>
                  <th>Logical Type</th>
                  <th>Semantic Type</th>
                </tr>
              </thead>
              <tbody>
                {inspect.columns.map((col) => (
                  <tr key={col.name}>
                    <td>{col.ordinal}</td>
                    <td>{col.name}</td>
                    <td className="mono">{col.physical_type}</td>
                    <td>{col.logical_type ?? "—"}</td>
                    <td>{col.semantic_type ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Preview panel */}
      {inspect && (
        <div className="preview-panel">
          <div className="preview-header">
            <h2>Preview</h2>
            <label className="mask-toggle">
              <input
                type="checkbox"
                checked={maskPii}
                onChange={(e) => {
                  setMaskPii(e.target.checked);
                  setOffset(0);
                }}
              />
              <span>Mask PII</span>
            </label>
            {!maskPii && (
              <span className="mask-warn">
                PII masking OFF — raw values may appear in the table below
              </span>
            )}
          </div>

          {preview.status === "error" && preview.error && (
            <div className="error-box">{preview.error}</div>
          )}

          {preview.status === "loading" && <p className="muted">Loading page…</p>}

          {preview.data && preview.status === "ready" && (
            <>
              <div className="table-scroll wide">
                <table>
                  <thead>
                    <tr>
                      {preview.data.columns.map((c) => (
                        <th key={c.name} title={c.physical_type}>
                          {c.name}
                          {c.masked && <span className="pii-tag" title="PII masked"> 🔒</span>}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.data.rows.map((row, ri) => (
                      <tr key={ri}>
                        {row.map((cell, ci) => (
                          <td key={ci}>{formatCell(cell)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Paging controls */}
              <div className="pager">
                <button
                  className="btn-sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  ← Prev
                </button>
                <span>
                  Rows {offset + 1}–{offset + preview.data.returned} of{" "}
                  {preview.data.total_rows.toLocaleString()}
                </span>
                <button
                  className="btn-sm"
                  disabled={offset + preview.data.returned >= preview.data.total_rows}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Next →
                </button>
              </div>

              {/* Masking summary */}
              {preview.data.masking_applied.length > 0 && (
                <p className="mask-info">
                  Masked columns: {preview.data.masking_applied.join(", ")}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

// --- Utility helpers ---

function formatBytes(n?: number): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "∅";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4);
  return String(v);
}
