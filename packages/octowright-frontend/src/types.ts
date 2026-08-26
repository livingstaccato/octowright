/** A browser engine core ships. Plugin kinds are NOT enumerable here. */
export type BrowserKind = "chromium" | "firefox" | "webkit";

/**
 * A session kind.
 *
 * Deliberately open: core knows its three browser engines, and every other
 * kind comes from a plugin whose name core cannot know at compile time. It
 * used to name `"terminal"` as a fourth member, which stopped being true when
 * terminal became a plugin -- and a CLOSED union was always wrong here, since
 * it could not type any plugin kind at all. The `(string & {})` arm keeps the
 * three literals visible to autocomplete while admitting the rest.
 */
export type Kind = BrowserKind | (string & {});

export interface OperationGateSnapshot {
  state: "open" | "closing" | "closed" | "broken";
  active_operation: string | null;
  active_for_ms: number | null;
  queue_depth: number;
  oldest_wait_ms: number | null;
  queue_timeout_seconds: number;
}

export interface SessionSummary {
  id: string;
  kind: Kind;
  label: string | null;
  profile: string | null;
  url: string | null;
  started_at: string;
  live: boolean;
  protected?: boolean;
  log_path: string;
  operation_gate?: OperationGateSnapshot;
}

export interface CacheComponent {
  size_bytes: number;
  size_human: string;
  path: string | null;
  exists: boolean;
}

export interface CacheComponentList {
  size_bytes: number;
  size_human: string;
  count: number;
  paths: string[];
}

export interface CacheReport {
  total_bytes: number;
  total_human: string;
  components: {
    jsonl: CacheComponent;
    markdown: CacheComponent;
    trace: CacheComponent;
    video: CacheComponent;
    websocket: CacheComponent;
    screenshots: CacheComponentList;
  };
  recommendations: string[];
}

export interface SessionDetail extends SessionSummary {
  video_path: string | null;
  trace_path: string | null;
  markdown_path: string | null;
  websocket_path: string | null;
  event_count: number;
  action_count: number;
  console_count: number;
  download_count: number;
  page_count: number;
  cache: CacheReport;
  aria?: string;
  macro_intent?: string;
  screencast?: {
    fps: number;
    quality: number;
    fullscreen_mode: "native" | "panel";
  };
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

export interface MacroAction {
  action: string;
  selector?: string;
  present?: boolean;
  then?: MacroAction[];
  else?: MacroAction[];
  branches?: MacroAction[][];
  [key: string]: unknown;
}

export interface MacroDetail {
  name: string;
  description: string | null;
  parameters: string[];
  created_at: string | null;
  updated_at: string | null;
  actions: MacroAction[];
  path?: string;
}

export interface MacroValidationIssue {
  code: string;
  message: string;
  severity: "error" | "warning" | string;
  action_index?: number;
  action?: MacroAction;
  details?: string;
}

export interface MacroValidationResponse {
  ok: boolean;
  valid?: boolean;
  issues: MacroValidationIssue[];
  issue_count?: number;
  error_count?: number;
  warning_count?: number;
  message?: string;
}

export interface MacroUpdateResponse {
  ok: boolean;
  name: string;
  path?: string;
  updated_at?: string | null;
}

export interface SelectorValidationResponse {
  ok: boolean;
  selector: string;
  found: boolean;
  count: number;
  error?: string;
}

export interface MacroRepairSuggestion {
  macro: string;
  action_index: number;
  original_action: Record<string, unknown>;
  source: string;
  replacement_action: Record<string, unknown> | null;
  action_preview: string | null;
  prompt: string;
}

export interface MacroRepairPreview {
  macro: string;
  suggestions: MacroRepairSuggestion[];
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
