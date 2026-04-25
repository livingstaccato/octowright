import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { setupTelemetry } from "@provide-io/telemetry";
import {
  apiErrorsCounter,
  apiLatencyHistogram,
  apiRequestsCounter,
  detectEnvironment,
  getLogger,
  initTelemetry,
  tabSwitchesCounter,
  userActionsCounter,
  wsConnectsCounter,
  wsMessagesCounter,
} from "./telemetry.js";

describe("telemetry", () => {
  beforeEach(() => {
    // Silence logs across the rest of the suite — pino accepts "silent" as a level.
    setupTelemetry({
      serviceName: "octowright-frontend-test",
      version: "test",
      environment: "test",
      logLevel: "silent",
      logFormat: "json",
      otelEnabled: false,
    });
  });

  afterEach(() => {
    setupTelemetry({
      serviceName: "octowright-frontend-test",
      version: "test",
      environment: "test",
      logLevel: "silent",
      logFormat: "json",
      otelEnabled: false,
    });
  });

  describe("initTelemetry", () => {
    it("is idempotent — multiple calls do not throw", () => {
      expect(() => {
        initTelemetry({ pageName: "test" });
        initTelemetry({ pageName: "test" });
        initTelemetry();
      }).not.toThrow();
    });
  });

  describe("detectEnvironment", () => {
    it("returns 'development' for localhost", () => {
      // jsdom default URL is http://localhost/, so this should hit the dev branch.
      expect(detectEnvironment()).toBe("development");
    });
  });

  describe("metric instruments", () => {
    it("apiLatencyHistogram is a recordable histogram", () => {
      expect(apiLatencyHistogram).toBeDefined();
      expect(() => apiLatencyHistogram.record(1, { path: "/foo", method: "GET", status: "200" })).not.toThrow();
    });

    it("counters expose .add() and accept attributes", () => {
      for (const ctr of [
        apiRequestsCounter,
        apiErrorsCounter,
        wsMessagesCounter,
        wsConnectsCounter,
        tabSwitchesCounter,
        userActionsCounter,
      ]) {
        expect(ctr).toBeDefined();
        expect(() => ctr.add(1, { x: "y" })).not.toThrow();
      }
    });
  });

  describe("getLogger", () => {
    it("returns a logger with the four standard methods", () => {
      const log = getLogger("test.module");
      expect(typeof log.debug).toBe("function");
      expect(typeof log.info).toBe("function");
      expect(typeof log.warn).toBe("function");
      expect(typeof log.error).toBe("function");
    });

    it("does not throw on structured payloads", () => {
      const log = getLogger("test.module");
      expect(() => log.info({ event: "test_event", foo: 1, bar: "baz" })).not.toThrow();
    });
  });
});
