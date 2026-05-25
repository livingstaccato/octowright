import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
    vi.unstubAllGlobals();
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
    it("returns 'development' for RFC1918 dashboard hosts", () => {
      vi.stubGlobal("window", { location: { hostname: "192.168.1.20" } });
      expect(detectEnvironment()).toBe("development");
    });
    it("returns 'production' for non-local hosts", () => {
      vi.stubGlobal("window", { location: { hostname: "octowright.example" } });
      expect(detectEnvironment()).toBe("production");
    });
  });

  describe("global handlers", () => {
    it("handle window error and unhandled rejection events", () => {
      initTelemetry({ pageName: "handlers" });
      const errorEvent = new Event("error") as ErrorEvent;
      Object.defineProperties(errorEvent, {
        message: { value: "boom" },
        filename: { value: "app.js" },
        lineno: { value: 1 },
        colno: { value: 2 },
      });
      const rejectionEvent = new Event("unhandledrejection") as PromiseRejectionEvent;
      Object.defineProperty(rejectionEvent, "reason", { value: "nope" });
      expect(() => {
        window.dispatchEvent(errorEvent);
        window.dispatchEvent(rejectionEvent);
      }).not.toThrow();
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
