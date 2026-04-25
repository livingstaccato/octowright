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

export async function fetchJson<T>(path: string, opts: FetchJsonOptions = {}): Promise<T> {
  const init: RequestInit = {
    method: opts.method ?? "GET",
    headers: { Accept: "application/json" },
  };
  if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers = { ...init.headers, "Content-Type": "application/json" };
  }
  if (opts.signal) {
    init.signal = opts.signal;
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    throw new ApiError(`request failed: ${res.status} ${res.statusText}`, res.status, path);
  }
  return (await res.json()) as T;
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
