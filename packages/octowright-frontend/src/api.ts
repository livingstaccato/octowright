import {
  apiErrorsCounter,
  apiLatencyHistogram,
  apiRequestsCounter,
  getLogger,
} from "./telemetry.js";
import type {
  ConsoleListResponse,
  DownloadListResponse,
  EventsResponse,
  HealthResponse,
  MacroSummary,
  PersonaSummary,
  ScenarioListResponse,
  ScreenshotListResponse,
  SessionDetail,
  SessionListResponse,
  TraceOpenResponse,
} from "./types.js";

const log = getLogger("octowright.frontend.api");

export class ApiError extends Error {
  override readonly name = "ApiError";
  constructor(
    message: string,
    readonly status: number,
    readonly path: string,
  ) {
    super(message);
  }
}

export interface FetchJsonOptions {
  method?: "GET" | "POST" | "DELETE" | "PATCH" | "PUT";
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Normalise a path so that variable IDs collapse to a templated form.
 * Keeps cardinality of metric attributes bounded.
 *
 *   /api/sessions/abc/events           → /api/sessions/{id}/events
 *   /api/sessions/abc/screenshots/x.png → /api/sessions/{id}/screenshots/{file}
 */
export function pathTemplate(path: string): string {
  // Strip query string for the template.
  const qIndex = path.indexOf("?");
  const bare = qIndex >= 0 ? path.slice(0, qIndex) : path;
  return bare
    .replace(/^(\/api\/sessions\/)[^/]+/, "$1{id}")
    .replace(/(\/screenshots\/)[^/]+$/, "$1{file}")
    .replace(/(\/frame)$/, "$1");
}

function nowMs(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

export async function fetchJson<T>(path: string, opts: FetchJsonOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const tmpl = pathTemplate(path);
  const init: RequestInit = {
    method,
    headers: { Accept: "application/json" },
  };
  if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers = { ...init.headers, "Content-Type": "application/json" };
  }
  if (opts.signal) {
    init.signal = opts.signal;
  }

  const start = nowMs();
  apiRequestsCounter.add(1, { method, path: tmpl });
  log.debug({ event: "api_request", method, path, path_template: tmpl });
  try {
    const res = await fetch(path, init);
    const duration = nowMs() - start;
    const statusStr = String(res.status);
    apiLatencyHistogram.record(duration, { method, path: tmpl, status: statusStr });
    if (!res.ok) {
      apiErrorsCounter.add(1, { method, path: tmpl, status: statusStr });
      log.warn({
        event: "api_error",
        method,
        path,
        path_template: tmpl,
        status: res.status,
        duration_ms: duration,
      });
      throw new ApiError(`request failed: ${res.status} ${res.statusText}`, res.status, path);
    }
    log.debug({
      event: "api_response",
      method,
      path,
      path_template: tmpl,
      status: res.status,
      duration_ms: duration,
    });
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    const duration = nowMs() - start;
    apiErrorsCounter.add(1, { method, path: tmpl, status: "exception" });
    log.error({
      event: "api_exception",
      method,
      path,
      path_template: tmpl,
      duration_ms: duration,
      error: String(err),
    });
    throw err;
  }
}

export function getSessions(): Promise<SessionListResponse> {
  return fetchJson<SessionListResponse>("/api/sessions");
}

export function getSession(id: string): Promise<SessionDetail> {
  return fetchJson<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`);
}

export function getEvents(id: string, since = 0): Promise<EventsResponse> {
  const qs = new URLSearchParams({ since: String(since) });
  return fetchJson<EventsResponse>(`/api/sessions/${encodeURIComponent(id)}/events?${qs.toString()}`);
}

export function getScenarios(): Promise<ScenarioListResponse> {
  return fetchJson<ScenarioListResponse>("/api/scenarios");
}

export function getPersonas(): Promise<PersonaSummary[]> {
  return fetchJson<PersonaSummary[]>("/api/personas");
}

export function getMacros(): Promise<MacroSummary[]> {
  return fetchJson<MacroSummary[]>("/api/macros");
}

export function getScreenshots(id: string): Promise<ScreenshotListResponse> {
  return fetchJson<ScreenshotListResponse>(`/api/sessions/${encodeURIComponent(id)}/screenshots`);
}

export function getConsole(id: string, since = 0, level?: string): Promise<ConsoleListResponse> {
  const qs = new URLSearchParams({ since: String(since) });
  if (level && level !== "all") qs.set("level", level);
  return fetchJson<ConsoleListResponse>(`/api/sessions/${encodeURIComponent(id)}/console?${qs.toString()}`);
}

export function getDownloads(id: string, since = 0): Promise<DownloadListResponse> {
  const qs = new URLSearchParams({ since: String(since) });
  return fetchJson<DownloadListResponse>(`/api/sessions/${encodeURIComponent(id)}/downloads?${qs.toString()}`);
}

export function openTrace(id: string): Promise<TraceOpenResponse> {
  return fetchJson<TraceOpenResponse>(`/api/sessions/${encodeURIComponent(id)}/trace/open`, { method: "POST" });
}

export function getHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>("/api/health");
}

export function videoUrl(id: string): string {
  return `/api/sessions/${encodeURIComponent(id)}/video`;
}

export function traceDownloadUrl(id: string): string {
  return `/api/sessions/${encodeURIComponent(id)}/trace`;
}

export function frameUrl(id: string, t: number): string {
  return `/api/sessions/${encodeURIComponent(id)}/frame?t=${encodeURIComponent(String(t))}`;
}

export function screenshotUrl(id: string, filename: string): string {
  return `/api/sessions/${encodeURIComponent(id)}/screenshots/${encodeURIComponent(filename)}`;
}

export function tailWebSocketUrl(id: string): string {
  const proto = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = typeof window !== "undefined" ? window.location.host : "localhost";
  return `${proto}//${host}/api/sessions/${encodeURIComponent(id)}/tail`;
}
