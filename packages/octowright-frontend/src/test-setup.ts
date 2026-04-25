// Vitest global setup: silence telemetry logs across the whole suite.
// Individual tests can still re-call setupTelemetry() if they need to assert
// on log output.
import { setupTelemetry } from "@provide-io/telemetry";

setupTelemetry({
  serviceName: "octowright-frontend-test",
  version: "test",
  environment: "test",
  logLevel: "silent",
  logFormat: "json",
  otelEnabled: false,
});
