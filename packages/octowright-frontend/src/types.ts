export type Kind = "chromium" | "firefox" | "webkit";

export interface SessionSummary {
  id: string;
  kind: Kind;
  label: string | null;
  profile: string | null;
  url: string | null;
  started_at: string;
  live: boolean;
  log_path: string;
}

export interface SessionDetail extends SessionSummary {
  video_path: string | null;
  trace_path: string | null;
  action_count: number;
  console_count: number;
  download_count: number;
  page_count: number;
  title: string | null;
}

export interface RecordingEvent {
  ts: string;
  action: string;
  [key: string]: unknown;
}

export interface EventsResponse {
  events: RecordingEvent[];
  cursor: number;
  total_bytes: number;
  complete: boolean;
}

export interface ScenarioParticipant {
  role: string;
  persona: string;
  kind: Kind;
  instance_id: string;
}

export interface LiveScenario {
  scenario_id: string;
  name: string;
  participants: ScenarioParticipant[];
}

export interface PersonaSummary {
  name: string;
  display_name: string | null;
  engines: string[];
  last_used: string;
}

export interface MacroSummary {
  name: string;
  description: string | null;
  parameters: string[];
  updated_at: string | null;
}

export interface ScreenshotEntry {
  path: string;
  filename: string;
  ts: number;
  size_bytes: number;
}

export type ConsoleLevel = "log" | "warn" | "error" | "info" | "debug" | string;

export interface ConsoleMessage {
  level: ConsoleLevel;
  text: string;
  page_index: number | null;
}

export interface ConsoleListResponse {
  messages: ConsoleMessage[];
  cursor: number;
  total: number;
}

export interface DownloadEntry {
  url: string;
  suggested_filename: string;
  path: string;
  timestamp: string;
  path_exists?: boolean;
  size_bytes?: number;
}

export interface DownloadListResponse {
  downloads: DownloadEntry[];
  cursor: number;
  total: number;
}

export interface SessionListResponse {
  live: SessionSummary[];
  closed: SessionSummary[];
}

export interface SavedScenario {
  name: string;
  path: string;
  form: "yaml" | "python";
  mtime: number;
}

export interface ScenarioListResponse {
  live: LiveScenario[];
  saved?: SavedScenario[];
}

export interface ScreenshotListResponse {
  screenshots: ScreenshotEntry[];
}

export interface TraceOpenResponse {
  pid: number;
  trace_path: string;
}

export interface HealthResponse {
  ok: boolean;
  version: string;
}

export interface PersonaDetail {
  name: string;
  yaml: string;
  path: string;
  disk_bytes: number;
  engine_bytes: Record<string, number>;
}
