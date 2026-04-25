// Telemetry bootstrap for the octowright frontend.
//
// Centralises @provide-io/telemetry setup so each entrypoint (dashboard,
// session) calls one function and gets a consistent service identity, log
// format, and global error handlers.
//
// Mirrors the Python sister package wiring used by `octowright serve`
// (`setup_telemetry()` once at startup, `get_logger(__name__)` per module).

import {
  bindContext,
  clearContext,
  counter,
  getLogger,
  histogram,
  setupTelemetry,
  unbindContext,
} from "@provide-io/telemetry";

const SERVICE_NAME = "octowright-frontend";
// Hardcoded for now; CLAUDE.md note: could be wired from package.json at build time later.
const VERSION = "0.1.0";

let _initialized = false;

export interface InitTelemetryOptions {
  /** Logical page name for diagnostics (e.g. "dashboard", "session"). */
  pageName?: string;
}

/**
 * Initialise telemetry for the current page. Safe to call more than once;
 * subsequent calls re-apply config but do not duplicate global handlers.
 */
export function initTelemetry(_opts: InitTelemetryOptions = {}): void {
  setupTelemetry({
    serviceName: SERVICE_NAME,
    version: VERSION,
    environment: detectEnvironment(),
    logLevel: detectLogLevel(),
    logFormat: detectLogFormat(),
    // OTEL exporter wiring is opt-in; leave disabled until the worker side
    // exposes a collector endpoint and we add the peer deps.
    otelEnabled: false,
  });
  if (!_initialized) {
    installGlobalHandlers();
    _initialized = true;
  }
}

export function detectEnvironment(): "production" | "development" {
  if (typeof window === "undefined") return "development";
  const h = window.location.hostname;
  if (h === "localhost" || h === "127.0.0.1" || h === "" || h.startsWith("192.168.")) {
    return "development";
  }
  return "production";
}

function detectLogLevel(): "debug" | "info" {
  return detectEnvironment() === "development" ? "debug" : "info";
}

function detectLogFormat(): "pretty" | "json" {
  return detectEnvironment() === "development" ? "pretty" : "json";
}

function installGlobalHandlers(): void {
  if (typeof window === "undefined") return;
  const log = getLogger("octowright.frontend.global");
  window.addEventListener("error", (e) => {
    log.error({
      event: "window_error",
      message: e.message,
      filename: e.filename,
      lineno: e.lineno,
      colno: e.colno,
    });
  });
  window.addEventListener("unhandledrejection", (e) => {
    log.error({ event: "unhandled_rejection", reason: String(e.reason) });
  });
}

// Re-export common helpers so callers only import from this module.
export { bindContext, clearContext, getLogger, unbindContext };

// Pre-create metric instruments so callers don't recreate them per call.
export const apiLatencyHistogram = histogram("octowright_frontend_api_latency_ms", {
  description: "API call latency in milliseconds",
  unit: "ms",
});

export const apiRequestsCounter = counter("octowright_frontend_api_requests_total", {
  description: "Total API requests issued by the frontend",
});

export const apiErrorsCounter = counter("octowright_frontend_api_errors_total", {
  description: "API requests that ended in a non-2xx status or threw",
});

export const wsMessagesCounter = counter("octowright_frontend_ws_messages_total", {
  description: "Total WebSocket messages received",
});

export const wsConnectsCounter = counter("octowright_frontend_ws_connects_total", {
  description: "Total WebSocket connect events",
});

export const tabSwitchesCounter = counter("octowright_frontend_tab_switches_total", {
  description: "Total per-session tab switches",
});

export const userActionsCounter = counter("octowright_frontend_user_actions_total", {
  description: "Total user-initiated actions (clicks, seeks, etc)",
});
