export function truncate(value: string, max: number): string {
  if (value.length <= max) return value;
  if (max <= 1) return value.slice(0, max);
  return `${value.slice(0, max - 1)}…`;
}

export function shortUrl(url: string | null | undefined, max = 60): string {
  if (!url) return "";
  let display = url;
  try {
    const parsed = new URL(url);
    const tail = `${parsed.pathname}${parsed.search}`;
    display = `${parsed.host}${tail === "/" ? "" : tail}`;
  } catch {
    // not a URL — display as-is
  }
  return truncate(display, max);
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").slice(0, 19);
}

export function formatBytes(n: number): string {
  if (n >= 1_073_741_824) return `${(n / 1_073_741_824).toFixed(1)} GB`;
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)} MB`;
  if (n >= 1_024) return `${(n / 1_024).toFixed(0)} KB`;
  return `${n} B`;
}

export type ActionColorKind = "navigate" | "click" | "fill" | "expect" | "error" | "default";

export function colorForAction(action: string): ActionColorKind {
  const a = action.toLowerCase();
  if (a === "navigate" || a === "goto" || a === "navigation") return "navigate";
  if (a === "click" || a === "dblclick") return "click";
  if (a === "fill" || a === "type" || a === "press_key") return "fill";
  if (a.startsWith("expect_") || a.startsWith("expect")) return "expect";
  if (a === "error" || a === "exception" || a === "failure") return "error";
  return "default";
}

const HEADLINE_KEYS = [
  "role",
  "role_name",
  "label",
  "test_id",
  "data_id",
  "data-id",
  "selector",
  "url",
  "text",
  "value",
  "key",
  "name",
  "filename",
  "message",
  "payload_preview",
];

function isLikelyBinaryPreview(value: unknown): boolean {
  return typeof value === "string" && ((value.startsWith("b\"") && value.endsWith("\"")) || (value.startsWith("b'") && value.endsWith("'")));
}

export function eventHeadline(event: Record<string, unknown>, max = 60): string {
  if (typeof event.action === "string" && event.action.startsWith("websocket_") && isLikelyBinaryPreview(event.payload_preview)) {
    return "[binary payload hidden]";
  }

  for (const key of HEADLINE_KEYS) {
    const v = event[key];
    if (typeof v === "string" && v.length > 0) {
      return truncate(v, max);
    }
  }
  return "";
}

export function relativeSeconds(iso: string, baseIso: string): number {
  const a = Date.parse(iso);
  const b = Date.parse(baseIso);
  if (Number.isNaN(a) || Number.isNaN(b)) return 0;
  return Math.max(0, (a - b) / 1000);
}
