import {
  getConsole,
  getDownloads,
  getEvents,
  getScreenshots,
  getSession,
  markdownUrl,
  openTrace,
  tailWebSocketUrl,
  traceDownloadUrl,
  videoUrl,
} from "./api.js";
import { renderConsolePanel } from "./console-panel.js";
import { renderDownloadsPanel } from "./downloads-panel.js";
import { renderScreenshotsPanel } from "./screenshots-panel.js";
import { formatDateTime } from "./format.js";
import { mountLivePreview } from "./live-preview.js";
import { openTail } from "./tail.js";
import {
  bindContext,
  getLogger,
  initTelemetry,
  tabSwitchesCounter,
  userActionsCounter,
} from "./telemetry.js";
import { appendTimelineEvents, renderTimeline } from "./timeline.js";
import type {
  CacheComponent,
  CacheComponentList,
  ConsoleMessage,
  DownloadEntry,
  RecordingEvent,
  ScreenshotEntry,
  SessionDetail,
} from "./types.js";

const log = getLogger("octowright.frontend.session");

type CacheRow =
  | { kind: "list"; label: string; component: CacheComponentList }
  | { kind: "component"; label: string; component: CacheComponent };

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
  livePreviewSlot: HTMLElement;
  timeline: HTMLElement;
  tabs: HTMLElement;
  consolePanel: HTMLElement;
  ariaPanel: HTMLElement;
  markdownPanel: HTMLElement;
  downloadsPanel: HTMLElement;
  screenshotsPanel: HTMLElement;
  cachePanel: HTMLElement;
  consoleTabBtn: HTMLButtonElement;
  ariaTabBtn: HTMLButtonElement;
  markdownTabBtn: HTMLButtonElement;
  downloadsTabBtn: HTMLButtonElement;
  screenshotsTabBtn: HTMLButtonElement;
  footer: HTMLElement;
}

export type PanelTab = "console" | "aria" | "markdown" | "downloads" | "screenshots";

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

  // Live preview sits ABOVE the timeline because when a flow is stuck the
  // user wants to see the page right now, then check the action history.
  const livePreviewSlot = document.createElement("section");
  livePreviewSlot.id = "live-preview-panel";
  livePreviewSlot.className = "session-live-preview";
  livePreviewSlot.setAttribute("data-testid", "live-preview-panel");

  const timeline = document.createElement("div");
  timeline.className = "session-timeline";
  timeline.setAttribute("data-testid", "session-timeline");

  const tabs = document.createElement("div");
  tabs.className = "session-tabs";
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("data-testid", "session-tabs");

  const consoleTabBtn = makeTabButton("console", "Console");
  const ariaTabBtn = makeTabButton("aria", "A11y Tree");
  const markdownTabBtn = makeTabButton("markdown", "Markdown");
  const downloadsTabBtn = makeTabButton("downloads", "Downloads");
  const screenshotsTabBtn = makeTabButton("screenshots", "Screenshots");
  tabs.append(consoleTabBtn, ariaTabBtn, markdownTabBtn, downloadsTabBtn, screenshotsTabBtn);

  const consolePanel = document.createElement("div");
  consolePanel.className = "session-panel session-panel--console";
  consolePanel.id = "console-panel";
  consolePanel.setAttribute("role", "tabpanel");
  consolePanel.setAttribute("data-tab", "console");

  const ariaPanel = document.createElement("div");
  ariaPanel.className = "session-panel session-panel--aria";
  ariaPanel.id = "aria-panel";
  ariaPanel.setAttribute("role", "tabpanel");
  ariaPanel.setAttribute("data-tab", "aria");

  const markdownPanel = document.createElement("div");
  markdownPanel.className = "session-panel session-panel--markdown";
  markdownPanel.id = "markdown-panel";
  markdownPanel.setAttribute("role", "tabpanel");
  markdownPanel.setAttribute("data-tab", "markdown");

  const downloadsPanel = document.createElement("div");
  downloadsPanel.className = "session-panel session-panel--downloads";
  downloadsPanel.id = "downloads-panel";
  downloadsPanel.setAttribute("role", "tabpanel");
  downloadsPanel.setAttribute("data-tab", "downloads");

  const screenshotsPanel = document.createElement("div");
  screenshotsPanel.className = "session-panel session-panel--screenshots";
  screenshotsPanel.id = "screenshots-panel";
  screenshotsPanel.setAttribute("role", "tabpanel");
  screenshotsPanel.setAttribute("data-tab", "screenshots");

  const cachePanel = document.createElement("div");
  cachePanel.className = "session-cache-summary";
  cachePanel.id = "cache-summary-panel";

  right.append(
    livePreviewSlot,
    timeline,
    tabs,
    consolePanel,
    ariaPanel,
    markdownPanel,
    downloadsPanel,
    screenshotsPanel,
    cachePanel,
  );

  const footer = document.createElement("footer");
  footer.className = "session-footer";
  footer.setAttribute("data-testid", "session-footer");

  root.append(left, right, footer);
  return {
    header,
    videoSlot,
    traceSlot,
    livePreviewSlot,
    timeline,
    tabs,
    consolePanel,
    ariaPanel,
    markdownPanel,
    downloadsPanel,
    screenshotsPanel,
    cachePanel,
    consoleTabBtn,
    ariaTabBtn,
    markdownTabBtn,
    downloadsTabBtn,
    screenshotsTabBtn,
    footer,
  };
}

function makeTabButton(name: PanelTab, label: string): HTMLButtonElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "session-tab";
  btn.setAttribute("role", "tab");
  btn.setAttribute("data-tab", name);
  btn.setAttribute("data-testid", `tab-${name}`);
  btn.dataset.label = label;
  btn.textContent = label;
  return btn;
}

export function setActiveTab(refs: PageRefs, tab: PanelTab): void {
  const map: Record<PanelTab, { btn: HTMLButtonElement; panel: HTMLElement }> = {
    console: { btn: refs.consoleTabBtn, panel: refs.consolePanel },
    aria: { btn: refs.ariaTabBtn, panel: refs.ariaPanel },
    markdown: { btn: refs.markdownTabBtn, panel: refs.markdownPanel },
    downloads: { btn: refs.downloadsTabBtn, panel: refs.downloadsPanel },
    screenshots: { btn: refs.screenshotsTabBtn, panel: refs.screenshotsPanel },
  };
  for (const key of Object.keys(map) as PanelTab[]) {
    const active = key === tab;
    map[key].btn.classList.toggle("session-tab--active", active);
    map[key].btn.setAttribute("aria-selected", String(active));
    map[key].panel.style.display = active ? "" : "none";
    map[key].panel.classList.toggle("session-panel--active", active);
  }
}

function setTabCount(btn: HTMLButtonElement, count: number): void {
  const label = btn.dataset.label ?? btn.textContent ?? "";
  btn.textContent = `${label} (${count})`;
}

export function renderAriaPanel(target: HTMLElement, detail: SessionDetail): void {
  target.innerHTML = "";
  if (!detail.aria) {
    const empty = document.createElement("div");
    empty.className = "session-panel__empty";
    empty.textContent = "No accessibility tree snapshot available for this session.";
    target.append(empty);
    return;
  }

  const pre = document.createElement("pre");
  pre.className = "aria-tree";
  pre.textContent = detail.aria;
  target.append(pre);
}

export function renderMarkdownPanel(target: HTMLElement, detail: SessionDetail): void {
  target.innerHTML = "";
  const details = document.createElement("details");
  details.className = "session-markdown-details";
  const summary = document.createElement("summary");
  summary.textContent = "Markdown snapshot";
  details.append(summary);
  target.append(details);

  if (!detail.markdown_path) {
    const empty = document.createElement("div");
    empty.className = "session-panel__empty";
    empty.textContent = "No markdown snapshot captured for this session yet.";
    details.append(empty);
    return;
  }

  const link = document.createElement("a");
  link.href = markdownUrl(detail.id);
  link.className = "btn btn--secondary";
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "Open markdown export";
  details.append(link);
}

export function renderCachePanel(target: HTMLElement, detail: SessionDetail): void {
  target.innerHTML = "";
  const details = document.createElement("details");
  details.className = "session-cache-details";
  details.open = false;

  const summary = document.createElement("summary");
  summary.textContent = `Cache summary (${detail.cache.total_human})`;
  details.append(summary);

  const body = document.createElement("div");
  body.className = "session-cache-details__body";
  const components = document.createElement("div");
  components.className = "session-cache-components";
  const rows: CacheRow[] = [
    { kind: "component", label: "JSONL", component: detail.cache.components.jsonl },
    { kind: "component", label: "Markdown", component: detail.cache.components.markdown },
    { kind: "component", label: "Trace", component: detail.cache.components.trace },
    { kind: "component", label: "Video", component: detail.cache.components.video },
    { kind: "component", label: "WebSocket", component: detail.cache.components.websocket },
    { kind: "list", label: "Screenshots", component: detail.cache.components.screenshots },
  ];

  rows.forEach(({ kind, label, component }) => {
    const row = document.createElement("p");
    row.className = "session-cache-row";
    if (kind === "list") {
      row.textContent = `${label}: ${component.count} files, ${component.size_human}`;
    } else {
      row.textContent = `${label}: ${component.exists ? component.size_human : "missing"}`;
      if (!component.exists) {
        row.classList.add("session-cache-row--missing");
      }
    }
    components.append(row);
  });

  body.append(components);
  if (detail.cache.recommendations.length > 0) {
    const recWrap = document.createElement("div");
    recWrap.className = "session-cache-recommendations";
    const title = document.createElement("strong");
    title.textContent = "Recommendations:";
    const list = document.createElement("ul");
    for (const rec of detail.cache.recommendations) {
      const li = document.createElement("li");
      li.textContent = rec;
      list.append(li);
    }
    recWrap.append(title, list);
    body.append(recWrap);
  }

  details.append(body);
  target.append(details);
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

  if (detail.macro_intent) {
    const intent = document.createElement("div");
    intent.className = "session-header__intent";
    // macro_intent is derived from JSONL fields (selectors, fill values,
    // URLs) — never trust it for innerHTML. Build the prefix via DOM so a
    // crafted selector can't execute script in the dashboard.
    const intentLabel = document.createElement("strong");
    intentLabel.textContent = "Intent:";
    intent.append(intentLabel, ` ${detail.macro_intent}`);
    meta.append(title, sub, when, intent);
  } else {
    meta.append(title, sub, when);
  }

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
    log.info({ event: "trace_open_clicked", session_id: detail.id });
    userActionsCounter.add(1, { action: "trace_open" });
    open.disabled = true;
    status.textContent = "opening…";
    openTrace(detail.id)
      .then((res) => {
        status.textContent = `pid ${res.pid}`;
        log.info({ event: "trace_open_success", session_id: detail.id, pid: res.pid });
      })
      .catch((err: unknown) => {
        status.textContent = `failed: ${(err as Error).message}`;
        log.error({ event: "trace_open_failed", session_id: detail.id, error: String(err) });
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

interface PanelData {
  console: ConsoleMessage[];
  downloads: DownloadEntry[];
  screenshots: ScreenshotEntry[];
}

async function loadConsole(sessionId: string): Promise<ConsoleMessage[]> {
  try {
    const res = await getConsole(sessionId);
    return res.messages;
  } catch {
    return [];
  }
}

async function loadDownloads(sessionId: string): Promise<DownloadEntry[]> {
  try {
    const res = await getDownloads(sessionId);
    return res.downloads;
  } catch {
    return [];
  }
}

async function loadScreenshots(sessionId: string): Promise<ScreenshotEntry[]> {
  try {
    const res = await getScreenshots(sessionId);
    return res.screenshots;
  } catch {
    return [];
  }
}

async function refreshPanels(
  sessionId: string,
  refs: PageRefs,
  data: PanelData,
  which: Array<keyof PanelData>,
): Promise<void> {
  const tasks: Array<Promise<void>> = [];
  if (which.includes("console")) {
    tasks.push(
      loadConsole(sessionId).then((msgs) => {
        data.console = msgs;
        renderConsolePanel(refs.consolePanel, msgs);
        setTabCount(refs.consoleTabBtn, msgs.length);
      }),
    );
  }
  if (which.includes("downloads")) {
    tasks.push(
      loadDownloads(sessionId).then((dls) => {
        data.downloads = dls;
        renderDownloadsPanel(refs.downloadsPanel, dls);
        setTabCount(refs.downloadsTabBtn, dls.length);
      }),
    );
  }
  if (which.includes("screenshots")) {
    tasks.push(
      loadScreenshots(sessionId).then((shots) => {
        data.screenshots = shots;
        renderScreenshotsPanel(refs.screenshotsPanel, sessionId, shots);
        setTabCount(refs.screenshotsTabBtn, shots.length);
      }),
    );
  }
  await Promise.all(tasks);
}

export async function bootSession(root: HTMLElement, sessionId: string, opts: BootOptions = {}): Promise<void> {
  log.info({ event: "session_boot_start", session_id: sessionId });
  const detail = await getSession(sessionId);
  log.info({
    event: "session_detail_loaded",
    session_id: sessionId,
    kind: detail.kind,
    live: detail.live,
    has_video: Boolean(detail.video_path),
    has_trace: Boolean(detail.trace_path),
  });

  // Terminal sessions get a dedicated slim layout (no video/trace/tabs). Branch
  // before the browser layout build so the browser path stays untouched.
  if (detail.kind === "terminal") {
    // Lazy-load the terminal view (xterm + addons, ~250KB) only for terminal
    // sessions so it never bloats the browser-session debugger bundle. Also
    // breaks the static session ↔ session-terminal import cycle.
    const { bootTerminalSession } = await import("./session-terminal.js");
    await bootTerminalSession(root, sessionId, detail, {
      ...(opts.webSocketCtor ? { webSocketCtor: opts.webSocketCtor } : {}),
    });
    log.info({ event: "session_boot_complete", session_id: sessionId, kind: "terminal" });
    return;
  }

  const refs = buildLayout(root);
  renderHeader(refs.header, detail);
  const videoEl = renderVideo(refs.videoSlot, detail);
  renderTraceControls(refs.traceSlot, detail);
  renderFooter(refs.footer, detail);

  // Live preview panel — polls /screenshot/now while session is live; shows a
  // closed-state placeholder otherwise. Stop on page unload to release the timer.
  const livePreview = mountLivePreview(refs.livePreviewSlot, {
    sessionId: detail.id,
    isLive: detail.live,
  });
  livePreview.start();
  window.addEventListener("beforeunload", () => livePreview.destroy());

  const data: PanelData = { console: [], downloads: [], screenshots: [] };

  // initial empty renders so counts/labels appear immediately
  renderConsolePanel(refs.consolePanel, data.console);
  renderDownloadsPanel(refs.downloadsPanel, data.downloads);
  renderScreenshotsPanel(refs.screenshotsPanel, sessionId, data.screenshots);
  renderAriaPanel(refs.ariaPanel, detail);
  renderMarkdownPanel(refs.markdownPanel, detail);
  renderCachePanel(refs.cachePanel, detail);
  setTabCount(refs.consoleTabBtn, detail.console_count ?? 0);
  setTabCount(refs.ariaTabBtn, detail.aria ? 1 : 0);
  setTabCount(refs.markdownTabBtn, detail.markdown_path ? 1 : 0);
  setTabCount(refs.downloadsTabBtn, detail.download_count ?? 0);
  setTabCount(refs.screenshotsTabBtn, 0);

  let currentTab: PanelTab = "console";
  const switchTab = (next: PanelTab): void => {
    const from = currentTab;
    if (from === next) return;
    log.info({ event: "tab_switch", from, to: next, session_id: sessionId });
    tabSwitchesCounter.add(1, { tab: next });
    userActionsCounter.add(1, { action: "tab_switch" });
    currentTab = next;
    setActiveTab(refs, next);
  };
  refs.consoleTabBtn.addEventListener("click", () => switchTab("console"));
  refs.ariaTabBtn.addEventListener("click", () => switchTab("aria"));
  refs.markdownTabBtn.addEventListener("click", () => switchTab("markdown"));
  refs.downloadsTabBtn.addEventListener("click", () => switchTab("downloads"));
  refs.screenshotsTabBtn.addEventListener("click", () => switchTab("screenshots"));
  setActiveTab(refs, "console");

  const seek = (seconds: number): void => {
    log.info({ event: "video_seek", session_id: sessionId, t: seconds });
    userActionsCounter.add(1, { action: "video_seek" });
    if (videoEl) videoEl.currentTime = seconds;
  };

  const initial = await getEvents(sessionId, 0);
  let baseIso = initial.events[0]?.ts ?? new Date().toISOString();
  renderTimeline(refs.timeline, initial.events, { onSeek: seek });

  // Fetch all three panels once on initial load (live + closed alike).
  await refreshPanels(sessionId, refs, data, ["console", "downloads", "screenshots"]);

  if (detail.live) {
    // Pass the history cursor so the tail starts AFTER what we already rendered.
    // Without this, the first WS frame replays the launch event a second time.
    const tail = openTail(tailWebSocketUrl(sessionId, initial.cursor), {
      onMessage: (msg) => {
        if (msg.events.length > 0) {
          if (initial.events.length === 0) {
            const first = msg.events[0];
            if (first) baseIso = first.ts;
          }
          appendTimelineEvents(refs.timeline, msg.events, baseIso, { onSeek: seek });
          // Cheap refresh: console + downloads counts may have changed.
          refreshPanels(sessionId, refs, data, ["console", "downloads"]).catch((err: unknown) => {
            log.warn({ event: "panel_refresh_failed", session_id: sessionId, error: String(err) });
          });
        }
        // The server flips ``complete: true`` when the live session
        // transitions to closed mid-connection. Stop polling /screenshot/now
        // immediately so the user doesn't see a stream of "transient error
        // (0)" indicators against a dead page.
        if (msg.complete) {
          livePreview.markClosed();
        }
      },
      ...(opts.webSocketCtor ? { webSocketCtor: opts.webSocketCtor } : {}),
    });
    window.addEventListener("beforeunload", () => tail.close());
  }
  log.info({ event: "session_boot_complete", session_id: sessionId });
}

export function appendForTest(events: RecordingEvent[], target: HTMLElement, baseIso: string): void {
  appendTimelineEvents(target, events, baseIso);
}

if (typeof document !== "undefined") {
  initTelemetry({ pageName: "session" });
  const root = document.getElementById("app");
  if (root) {
    const id = sessionIdFromPath(window.location.pathname);
    if (!id) {
      log.warn({ event: "session_invalid_url", pathname: window.location.pathname });
      root.textContent = "Invalid session URL";
    } else {
      // Tag every subsequent log record with this session id.
      bindContext({ session_id: id });
      log.info({ event: "page_load", page: "session", session_id: id });
      window.addEventListener("beforeunload", () => {
        log.info({ event: "page_unload", page: "session", session_id: id });
      });
      bootSession(root, id).catch((err: unknown) => {
        log.error({ event: "session_boot_failed", session_id: id, error: String(err) });
        root.textContent = `Session failed to load: ${(err as Error).message}`;
      });
    }
  } else {
    log.warn({ event: "session_root_missing" });
  }
}
