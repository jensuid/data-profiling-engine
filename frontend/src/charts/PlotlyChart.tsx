/**
 * M6-T5 — renders one Plotly {@link PlotlySpec} figure into a div.
 *
 * The vendored Plotly bundle is loaded lazily (once) via {@link loadPlotly};
 * while it is absent the component shows a placeholder rather than throwing, so
 * a chart never blocks the rest of the report (§6.4 surface-what-works). The
 * figure is redrawn whenever the spec identity changes and purged on unmount.
 *
 * ``displayModeBar:false`` keeps the chart quiet (the report is read-only
 * against its own numbers); ``responsive:true`` fits the panel width. Both are
 * render-only presentation flags — they change nothing about the data shown.
 */
import { useEffect, useRef, useState } from "react";
import { loadPlotly, type PlotlySpec } from "./plotly";

const PLOTLY_CONFIG = { displayModeBar: false, responsive: true };

export default function PlotlyChart({ spec }: { spec: PlotlySpec }) {
  const elRef = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const el = elRef.current;
    if (!el) return;
    loadPlotly()
      .then((Plotly) => {
        if (cancelled || !elRef.current) return;
        const draw = Plotly.react ?? Plotly.newPlot;
        return draw.call(Plotly, elRef.current, spec.data, spec.layout, PLOTLY_CONFIG);
      })
      .then(() => setErr(null))
      .catch((e: unknown) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [spec]);

  useEffect(() => {
    // Purge on unmount so detached charts don't leak resize observers.
    const el = elRef.current;
    return () => {
      if (el && window.Plotly?.purge) {
        try {
          window.Plotly.purge(el);
        } catch {
          /* ignore teardown races */
        }
      }
    };
  }, []);

  return (
    <div className="plotly-wrap">
      <div ref={elRef} className="plotly-chart" />
      {err && <p className="mask-warn">{err}</p>}
    </div>
  );
}
