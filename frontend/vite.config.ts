import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" → relative asset paths, required because production loads
// dist/index.html over file:// with no local HTTP server (ADR-005/010).
export default defineConfig({
  base: "./",
  plugins: [react()],
  // Reuse the ONE vendored Plotly bundle the HTML report also inlines
  // (reporting/assets/plotly.min.js) rather than duplicating 4.8 MB into the
  // repo: vite copies it verbatim to dist root (dev: served at /plotly.min.js),
  // and the UI loads it with a document-relative <script src="plotly.min.js">
  // that resolves correctly under file://. No CDN, no network (ADR-005/009/010).
  publicDir: "../data_profiling_engine/reporting/assets",
  build: {
    outDir: "dist",
    assetsInlineLimit: 0,
  },
});
