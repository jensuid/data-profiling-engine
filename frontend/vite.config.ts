import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" → relative asset paths, required because production loads
// dist/index.html over file:// with no local HTTP server (ADR-005/010).
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "dist",
    assetsInlineLimit: 0,
  },
});
