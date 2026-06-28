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
  MacroDetail,
  MacroUpdateResponse,
  MacroRepairPreview,
  MacroValidationResponse,
  MacroSummary,
  PersonaDetail,
  PersonaSummary,
  ScenarioListResponse,
  ScenarioParticipant,
  ScreenshotListResponse,
  SessionDetail,
  SessionListResponse,
  SessionSummary,
  TraceOpenResponse,
  SelectorValidationResponse,
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
    .replace(/(\/screenshot\/now)$/, "$1")
    .replace(/(\/frame)$/, "$1");
}

function nowMs(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

async function responseErrorMessage(res: Response): Promise<string> {
  const fallback = `request failed: ${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as unknown;
    if (body && typeof body === "object") {
      const record = body as Record<string, unknown>;
      if (typeof record.error === "string" && record.error.trim()) {
        return `${fallback}: ${record.error}`;
      }
      if (typeof record.message === "string" && record.message.trim()) {
        return `${fallback}: ${record.message}`;
      }
    }
  } catch {
    // Fall back to the status line when the error body is not JSON.
  }
  return fallback;
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
      const message = await responseErrorMessage(res);
      apiErrorsCounter.add(1, { method, path: tmpl, status: statusStr });
      log.warn({
        event: "api_error",
        method,
        path,
        path_template: tmpl,
        status: res.status,
        duration_ms: duration,
      });
      throw new ApiError(message, res.status, path);
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

export function getMacro(name: string): Promise<MacroDetail> {
  return fetchJson<MacroDetail>(`/api/macros/${encodeURIComponent(name)}`);
}

export function getMacroRepairPreview(name: string): Promise<MacroRepairPreview> {
  return fetchJson<MacroRepairPreview>(`/api/macros/${encodeURIComponent(name)}/repair_preview`);
}

export function validateMacro(name: string, macro: unknown): Promise<MacroValidationResponse> {
  return fetchJson<MacroValidationResponse>(`/api/macros/${encodeURIComponent(name)}/validate`, {
    method: "POST",
    body: { macro },
  });
}

export function updateMacro(name: string, macro: unknown): Promise<MacroUpdateResponse> {
  return fetchJson<MacroUpdateResponse>(`/api/macros/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: { macro },
  });
}

export function validateSessionSelector(
  sessionId: string,
  selector: string,
): Promise<SelectorValidationResponse> {
  return fetchJson<SelectorValidationResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/selector/validate`, {
    method: "POST",
    body: { selector },
  });
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

export function dashboardEventsUrl(): string {
  return "/api/dashboard/events";
}

export function getPersonaDetail(name: string): Promise<PersonaDetail> {
  return fetchJson<PersonaDetail>(`/api/personas/${encodeURIComponent(name)}`);
}

export function updatePersonaYaml(name: string, yaml: string): Promise<{ ok: boolean; name: string }> {
  return fetchJson<{ ok: boolean; name: string }>(`/api/personas/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: { yaml },
  });
}

export function deleteRecording(
  id: string,
): Promise<{ deleted: boolean; session_id: string; files_removed: number }> {
  return fetchJson<{ deleted: boolean; session_id: string; files_removed: number }>(
    `/api/sessions/${encodeURIComponent(id)}/recording`,
    { method: "DELETE" },
  );
}

export function relaunchSession(id: string): Promise<SessionSummary> {
  return fetchJson<SessionSummary>(`/api/sessions/${encodeURIComponent(id)}/relaunch`, {
    method: "POST",
  });
}

export function startScenario(name: string): Promise<{
  scenario_id: string;
  name: string;
  participants: ScenarioParticipant[];
}> {
  return fetchJson<{
    scenario_id: string;
    name: string;
    participants: ScenarioParticipant[];
  }>(`/api/scenarios/${encodeURIComponent(name)}/start`, { method: "POST" });
}

export function getPersonaSizes(): Promise<Record<string, number | null>> {
  return fetchJson<Record<string, number | null>>("/api/personas/sizes");
}

export function videoUrl(id: string): string {
  return `/api/sessions/${encodeURIComponent(id)}/video`;
}

export function traceDownloadUrl(id: string): string {
  return `/api/sessions/${encodeURIComponent(id)}/trace`;
}

export function markdownUrl(id: string): string {
  return `/api/sessions/${encodeURIComponent(id)}/markdown`;
}

export function frameUrl(id: string, t: number): string {
  return `/api/sessions/${encodeURIComponent(id)}/frame?t=${encodeURIComponent(String(t))}`;
}

export interface LiveScreenshotOptions {
  format?: "png" | "jpeg";
  quality?: number;
  fullPage?: boolean;
  /** Cache-bust value, normally Date.now(). */
  cacheBust?: number;
}

/**
 * Build a URL for the live screenshot endpoint. Returns a path the browser
 * can hand straight to an `<img>` element; cache-busting via `_=<ts>` forces
 * a fresh fetch on every poll tick.
 */
export function liveScreenshotUrl(id: string, opts: LiveScreenshotOptions = {}): string {
  const qs = new URLSearchParams();
  qs.set("format", opts.format ?? "png");
  if ((opts.format ?? "png") === "jpeg" && opts.quality !== undefined) {
    qs.set("quality", String(opts.quality));
  }
  if (opts.fullPage !== undefined) {
    qs.set("full_page", String(opts.fullPage));
  }
  if (opts.cacheBust !== undefined) {
    qs.set("_", String(opts.cacheBust));
  }
  return `/api/sessions/${encodeURIComponent(id)}/screenshot/now?${qs.toString()}`;
}

export function screenshotUrl(id: string, filename: string): string {
  return `/api/sessions/${encodeURIComponent(id)}/screenshots/${encodeURIComponent(filename)}`;
}

export function tailWebSocketUrl(id: string, since = 0): string {
  const proto = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = typeof window !== "undefined" ? window.location.host : "localhost";
  const qs = since > 0 ? `?since=${since}` : "";
  return `${proto}//${host}/api/sessions/${encodeURIComponent(id)}/tail${qs}`;
}

export function screencastWsUrl(id: string, opts: { fps?: number } = {}): string {
  const proto = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = typeof window !== "undefined" ? window.location.host : "localhost";
  const qs = opts.fps !== undefined ? `?fps=${encodeURIComponent(String(opts.fps))}` : "";
  return `${proto}//${host}/api/sessions/${encodeURIComponent(id)}/screencast${qs}`;
}
