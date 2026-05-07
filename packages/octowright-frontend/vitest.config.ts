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
      // Ratchet thresholds — tuned just below current. Raise as coverage
      // improves; never lower silently. Current: stmts 93.7 / branches 77.5
      // / funcs 86.0 / lines 95.0 after the session.ts and dashboard.ts
      // backfill (commits adding session-boot.test.ts + dashboard-extra.test.ts).
      thresholds: {
        statements: 93,
        branches: 77,
        functions: 85,
        lines: 94,
      },
    },
  },
});
