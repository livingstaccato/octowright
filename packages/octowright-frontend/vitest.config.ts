import { defineConfig } from "vitest/config";
import { otelOptionalStub } from "./vite-otel-stub";

export default defineConfig({
  plugins: [otelOptionalStub()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "tests/**/*.test.ts"],
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
      // Ratchet thresholds — tuned just below current to catch regressions
      // without blocking on under-tested surfaces (session.ts, dashboard.ts).
      // Raise these as coverage improves; never lower them silently.
      thresholds: {
        statements: 76,
        branches: 66,
        functions: 64,
        lines: 78,
      },
    },
  },
});
