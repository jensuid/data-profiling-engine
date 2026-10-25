/**
 * M6-T5 — client ambient declarations for the Plotly chart path (§3.3).
 *
 * Plotly is NOT an npm dependency: the app is frozen/offline (ADR-009/010) and
 * a packaged build must render charts with zero network access, exactly like
 * the HTML report inlines the SAME vendored ``reporting/assets/plotly.min.js``
 * (no CDN tag — ADR-005). The UI loads that one already-vendored bundle (served
 * from Vite's ``publicDir``) as a plain script and talks to its global
 * ``window.Plotly``.
 *
 * ``vite/client`` is not wired into this tsconfig (include = ["src"] only), so
 * the ``Plotly`` global the chart renderer uses is declared here.
 */

/** Minimal surface of the global Plotly object the chart renderer uses. */
interface PlotlyStatic {
  newPlot: (
    el: HTMLElement,
    data: unknown[],
    layout?: Record<string, unknown>,
    config?: Record<string, unknown>,
  ) => Promise<unknown>;
  react?: (
    el: HTMLElement,
    data: unknown[],
    layout?: Record<string, unknown>,
    config?: Record<string, unknown>,
  ) => Promise<unknown>;
  purge?: (el: HTMLElement) => void;
}

interface Window {
  Plotly?: PlotlyStatic;
}
