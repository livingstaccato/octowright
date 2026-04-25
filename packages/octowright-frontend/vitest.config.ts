import { defineConfig, type Plugin } from "vitest/config";

// @provide-io/telemetry's optional OTEL exporter peers are NOT installed
// (we keep otelEnabled=false). The library guards them behind dynamic imports
// inside try/catch, but Vite's import-analysis still tries to resolve them
// during dev/test transform. Stub them to an empty module so transform succeeds;
// the stub is never executed because otelEnabled is false at runtime.
const OTEL_OPTIONAL_DEPS = [
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

const otelStubPlugin: Plugin = {
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

export default defineConfig({
  plugins: [otelStubPlugin],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
    setupFiles: ["src/test-setup.ts"],
    // @provide-io/telemetry@0.3.0 uses extensionless ESM imports in dist/;
    // route it through Vite's resolver, which tolerates them.
    server: {
      deps: {
        inline: ["@provide-io/telemetry"],
      },
    },
    coverage: {
      provider: "v8",
      reporter: ["text", "json"],
      reportsDirectory: "./coverage",
      include: ["src/**/*.ts"],
      exclude: ["src/**/*.test.ts", "src/test-setup.ts"],
    },
  },
});
