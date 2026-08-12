import { resolve } from "node:path";
import { defineConfig } from "vite";
import { otelOptionalStub } from "./vite-otel-stub";

// Production build of the octowright dashboard frontend.
//
// The Python server's StaticFiles mount serves whatever lands in
// `src/octowright/server/frontend/`, so that's our outDir. We use FLAT output
// (no `assets/` subdir, no hash suffixes) on purpose:
//   * the existing _serve_session_html SPA fallback hard-codes `session.html`
//   * pyproject's package_data globs the directory; flat names keep CI diffs
//     small and the hand-curated debug workflow (open one .js in devtools)
//     stays familiar.
//
// Multi-entry: each HTML in static/ is an entry; Vite walks the
// `<script type="module" src="../src/<name>.ts">` reference, bundles the
// transitive module graph, resolves bare specifiers (the bug this migration
// fixes — browsers can't resolve `@provide-io/telemetry` without a bundler),
// and rewrites the script src in the emitted HTML to the bundled name.

export default defineConfig({
  // Treat static/ as the project root so the emitted HTML lives at outDir/
  // (next to JS/CSS) rather than outDir/static/. Without this, Vite preserves
  // the input path and the Python StaticFiles mount would 404 on `/`.
  root: resolve(__dirname, "static"),
  build: {
    // outDir is resolved relative to `root`, so go up two extra levels.
    outDir: process.env.OCTOWRIGHT_FRONTEND_OUTDIR ?? "../../../src/octowright/server/frontend",
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: {
        index: resolve(__dirname, "static/index.html"),
        session: resolve(__dirname, "static/session.html"),
        "dashboard-media-sw": resolve(__dirname, "static/dashboard-media-sw.js"),
      },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "[name].js",
        // Vite names extracted shared CSS after an arbitrary JS chunk (currently
        // format.css), not after static/styles.css. The dashboard has exactly one
        // eager stylesheet plus the lazy terminal chunk, so name the non-terminal
        // CSS by the stable server/wheel contract instead of that chunk heuristic.
        assetFileNames: (assetInfo) => {
          const cssName = assetInfo.names.find((name) => name.endsWith(".css"));
          return cssName && cssName !== "session-terminal.css" ? "styles.css" : "[name][extname]";
        },
      },
    },
  },
  // Same OTEL peer-dep stubbing as vitest, so production builds don't
  // fail trying to resolve nine optional packages we never actually run.
  plugins: [otelOptionalStub()],
});
