import { getEvents, getSession, openTrace, tailWebSocketUrl, traceDownloadUrl, videoUrl } from "./api.js";
import { formatDateTime } from "./format.js";
import { openTail } from "./tail.js";
import { appendTimelineEvents, renderTimeline } from "./timeline.js";
import type { RecordingEvent, SessionDetail } from "./types.js";

export function sessionIdFromPath(pathname: string): string | null {
  const match = /^\/sessions\/([^/?#]+)/.exec(pathname);
  if (!match) return null;
  const raw = match[1];
  if (!raw) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

interface PageRefs {
  header: HTMLElement;
  videoSlot: HTMLElement;
  traceSlot: HTMLElement;
  timeline: HTMLElement;
  footer: HTMLElement;
}

export function buildLayout(root: HTMLElement): PageRefs {
  root.innerHTML = "";
  root.classList.add("session-page");
  const left = document.createElement("section");
  left.className = "session-page__left";
  left.setAttribute("data-testid", "session-left");
  const right = document.createElement("section");
  right.className = "session-page__right";
  right.setAttribute("data-testid", "session-right");

  const header = document.createElement("header");
  header.className = "session-header";
  header.setAttribute("data-testid", "session-header");

  const videoSlot = document.createElement("div");
  videoSlot.className = "session-video";
  videoSlot.setAttribute("data-testid", "session-video");

  const traceSlot = document.createElement("div");
  traceSlot.className = "session-trace";
  traceSlot.setAttribute("data-testid", "session-trace");

  left.append(header, videoSlot, traceSlot);

  const timeline = document.createElement("div");
  timeline.className = "session-timeline";
  timeline.setAttribute("data-testid", "session-timeline");

  right.append(timeline);

  const footer = document.createElement("footer");
  footer.className = "session-footer";
  footer.setAttribute("data-testid", "session-footer");

  root.append(left, right, footer);
  return { header, videoSlot, traceSlot, timeline, footer };
}

export function renderHeader(target: HTMLElement, detail: SessionDetail): void {
  target.innerHTML = "";
  const icon = document.createElement("span");
  icon.className = `kind-icon kind-icon--${detail.kind}`;
  icon.textContent = detail.kind.charAt(0).toUpperCase();

  const meta = document.createElement("div");
  meta.className = "session-header__meta";
  const title = document.createElement("h1");
  title.className = "session-header__title";
  title.textContent = detail.label ?? detail.profile ?? detail.id;
  const sub = document.createElement("div");
  sub.className = "session-header__sub";
  sub.textContent = `${detail.id} · ${detail.url ?? ""}`;
  const when = document.createElement("div");
  when.className = "session-header__when";
  when.textContent = `started ${formatDateTime(detail.started_at)}`;
  meta.append(title, sub, when);

  const status = document.createElement("span");
  status.className = `status status--${detail.live ? "live" : "closed"}`;
  status.textContent = detail.live ? "LIVE" : "CLOSED";

  target.append(icon, meta, status);
}

export function renderVideo(target: HTMLElement, detail: SessionDetail): HTMLVideoElement | null {
  target.innerHTML = "";
  if (detail.live && !detail.video_path) {
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = "video will be available after session closes";
    target.append(note);
    return null;
  }
  if (!detail.video_path) {
    const note = document.createElement("p");
    note.className = "note note--missing";
    note.textContent = "no video recorded for this session";
    target.append(note);
    return null;
  }
  const video = document.createElement("video");
  video.setAttribute("controls", "");
  video.setAttribute("preload", "metadata");
  video.setAttribute("data-testid", "video-player");
  video.src = videoUrl(detail.id);
  target.append(video);
  return video;
}

export function renderTraceControls(target: HTMLElement, detail: SessionDetail): void {
  target.innerHTML = "";
  if (!detail.trace_path) {
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = "no trace recorded";
    target.append(note);
    return;
  }
  const open = document.createElement("button");
  open.type = "button";
  open.className = "btn btn--primary";
  open.setAttribute("data-testid", "btn-open-trace");
  open.textContent = "Open trace in Playwright viewer";
  const status = document.createElement("span");
  status.className = "trace-status";
  status.setAttribute("data-testid", "trace-status");
  open.addEventListener("click", () => {
    open.disabled = true;
    status.textContent = "opening…";
    openTrace(detail.id)
      .then((res) => {
        status.textContent = `pid ${res.pid}`;
      })
      .catch((err: unknown) => {
        status.textContent = `failed: ${(err as Error).message}`;
      })
      .finally(() => {
        open.disabled = false;
      });
  });
  const dl = document.createElement("a");
  dl.href = traceDownloadUrl(detail.id);
  dl.className = "btn btn--secondary";
  dl.textContent = "Download .zip";
  dl.setAttribute("download", "");
  target.append(open, status, dl);
}

export function renderFooter(target: HTMLElement, detail: SessionDetail): void {
  target.innerHTML = "";
  if (detail.live) {
    target.textContent = "Refreshing every 1s";
  } else {
    target.textContent = `Closed at ${formatDateTime(detail.started_at)}`;
  }
}

interface BootOptions {
  webSocketCtor?: typeof WebSocket;
}

export async function bootSession(root: HTMLElement, sessionId: string, opts: BootOptions = {}): Promise<void> {
  const refs = buildLayout(root);
  const detail = await getSession(sessionId);
  renderHeader(refs.header, detail);
  const videoEl = renderVideo(refs.videoSlot, detail);
  renderTraceControls(refs.traceSlot, detail);
  renderFooter(refs.footer, detail);

  const seek = (seconds: number): void => {
    if (videoEl) videoEl.currentTime = seconds;
  };

  const initial = await getEvents(sessionId, 0);
  let baseIso = initial.events[0]?.ts ?? new Date().toISOString();
  renderTimeline(refs.timeline, initial.events, { onSeek: seek });

  if (detail.live) {
    const tail = openTail(tailWebSocketUrl(sessionId), {
      onMessage: (msg) => {
        if (msg.events.length === 0) return;
        if (initial.events.length === 0) {
          const first = msg.events[0];
          if (first) baseIso = first.ts;
        }
        appendTimelineEvents(refs.timeline, msg.events, baseIso, { onSeek: seek });
      },
      ...(opts.webSocketCtor ? { webSocketCtor: opts.webSocketCtor } : {}),
    });
    window.addEventListener("beforeunload", () => tail.close());
  }
}

export function appendForTest(events: RecordingEvent[], target: HTMLElement, baseIso: string): void {
  appendTimelineEvents(target, events, baseIso);
}

if (typeof document !== "undefined") {
  const root = document.getElementById("app");
  if (root) {
    const id = sessionIdFromPath(window.location.pathname);
    if (!id) {
      root.textContent = "Invalid session URL";
    } else {
      bootSession(root, id).catch((err: unknown) => {
        root.textContent = `Session failed to load: ${(err as Error).message}`;
      });
    }
  }
}
