// Shared Vite plugin: stub @provide-io/telemetry's optional OTEL peer deps.
//
// @provide-io/telemetry guards these imports behind dynamic import + try/catch
// (otelEnabled=false in our config), but Vite's import-analysis still tries to
// resolve the bare specifiers at transform/build time. None of the packages are
// installed (they're optional peers), so without this plugin both `vitest run`
// and `vite build` blow up on resolution.
//
// The stub returns a throwing-Proxy module: import succeeds (resolution OK), but
// any actual property access throws. Because otelEnabled=false at runtime the
// library never executes the dynamic import, so the throw is never triggered.
//
// Imported by both `vite.config.ts` (production build) and `vitest.config.ts`
// (test runner) so the two stay in sync.

import type { Plugin } from "vite";

export const OTEL_OPTIONAL_DEPS: readonly string[] = [
  "@opentelemetry/api-logs",
  "@opentelemetry/context-async-hooks",
  "@opentelemetry/exporter-logs-otlp-http",
  "@opentelemetry/exporter-metrics-otlp-http",
  "@opentelemetry/exporter-trace-otlp-http",
  "@opentelemetry/resources",
  "@opentelemetry/sdk-logs",
  "@opentelemetry/sdk-metrics",
  "@opentelemetry/sdk-trace-base",
];

export function otelOptionalStub(): Plugin {
  return {
    name: "octowright:otel-optional-stub",
    enforce: "pre",
    resolveId(source) {
      if (OTEL_OPTIONAL_DEPS.includes(source)) {
        return `\0otel-stub:${source}`;
      }
      return null;
    },
    load(id) {
      if (id.startsWith("\0otel-stub:")) {
        // Throw on actual use so the library's try/catch falls back to no-op.
        return "export default new Proxy({}, { get() { throw new Error('OTEL exporter not installed'); } });";
      }
      return null;
    },
  };
}
