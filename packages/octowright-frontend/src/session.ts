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
import {
  DASHBOARD_AUTH_REQUIRED_EVENT,
  downloadDashboardMedia,
  getDashboardBearer,
  handleDashboardUnauthorized,
  isolateDashboardTabAuth,
} from "./dashboard-auth.js";
import { clearDashboardMediaAuth, configureDashboardMediaAuth } from "./dashboard-media-auth.js";
import { renderDownloadsPanel } from "./downloads-panel.js";
import { formatDateTime } from "./format.js";
import { mountLivePreview } from "./live-preview.js";
import type { MountStream, StreamContext } from "./plugin-contract.js";
import { loadPluginRegistry, resolveRenderer } from "./plugin-registry.js";
import { disposeScreenshotsPanel, renderScreenshotsPanel } from "./screenshots-panel.js";
import { mountFallbackStream } from "./session-fallback.js";
import { openTail } from "./tail.js";
import { bindContext, getLogger, initTelemetry, tabSwitchesCounter, userActionsCounter } from "./telemetry.js";
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

// Mirrors `RESERVED_KINDS` in src/octowright/plugins/identity.py -- the set
// of session kinds core owns. `validate_kind()` (called from
// `plugins/loader.py` at load time) REFUSES to let any enabled plugin
// declare one of these, and that refusal is what makes it safe to skip the
// plugin-registry lookup (and its `/api/plugins` network round trip) for a
// kind in this set: a plugin can never claim "unknown" or "chromium", so a
// closed recording with no readable kind (a launch that died before writing
// its row, a truncated recording, a legacy file -- see
// `http/discovery.py`'s `opening.get("kind") or "unknown"`) is core's to
// render, not a plugin's. A future refactor "simplifying" this
// check-before-fetch ordering away would reintroduce a network fetch on
// every ordinary browser-session page load and break the pinned test in
// session.test.ts.
//
// `terminal` is deliberately absent from both sets: it is a plugin kind
// (see AGENTS.md's "Terminal Sessions" section) rather than a core-reserved
// one, so it flows through the registry-driven dispatch below like any
// other plugin kind instead of being added here.
//
// This is a hand-maintained mirror, not a generated one -- Python's
// RESERVED_KINDS lives in a different language entirely, so nothing keeps
// the two in sync automatically. Copy the Python set exactly rather than
// re-deriving one from `Kind` (types.ts): a compile-time-only type has no
// runtime representation, and `Kind` and RESERVED_KINDS answer different
// questions ("what browser kinds does the SPA know how to render" vs. "what
// kinds can no plugin ever claim"). If RESERVED_KINDS changes, update this
// too.
const CORE_RESERVED_KINDS: ReadonlySet<string> = new Set([
  "chromium",
  "firefox",
  "webkit",
  "browser",
  "unknown",
  "session",
]);

function safeDownloadName(value: string): string {
  return value.replace(/[^A-Za-z0-9._-]+/g, "-");
}

function bindProtectedDownload(
  link: HTMLAnchorElement,
  path: string,
  filename: string,
  statusTarget: HTMLElement,
): void {
  if (getDashboardBearer() === null) {
    link.href = path;
    return;
  }
  link.href = "#";
  link.addEventListener("click", (event) => {
    event.preventDefault();
    if (link.getAttribute("aria-busy") === "true") return;
    link.setAttribute("aria-busy", "true");
    statusTarget.textContent = "preparing download…";
    void downloadDashboardMedia(path, filename)
      .then(() => {
        statusTarget.textContent = "download ready";
      })
      .catch((error: unknown) => {
        statusTarget.setAttribute("role", "alert");
        statusTarget.textContent = `download failed: ${(error as Error).message}`;
      })
      .finally(() => link.removeAttribute("aria-busy"));
  });
}

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
  link.className = "btn btn--secondary";
  link.setAttribute("download", "");
  link.textContent = "Download markdown export";
  const status = document.createElement("span");
  status.className = "markdown-status";
  bindProtectedDownload(link, markdownUrl(detail.id), `${safeDownloadName(detail.id)}.md`, status);
  details.append(link, status);
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
  if (getDashboardBearer() === null) video.src = videoUrl(detail.id);
  target.append(video);
  return video;
}

interface ProtectedVideoOptions {
  signal?: AbortSignal;
  configureMediaAuth?: typeof configureDashboardMediaAuth;
  clearMediaAuth?: typeof clearDashboardMediaAuth;
}

export async function loadProtectedVideo(
  target: HTMLElement,
  video: HTMLVideoElement,
  sessionId: string,
  options: ProtectedVideoOptions = {},
): Promise<() => void> {
  const path = videoUrl(sessionId);
  const bearer = getDashboardBearer();
  const clearMediaAuth = options.clearMediaAuth ?? clearDashboardMediaAuth;
  let cleaned = false;
  let authRequired = false;
  const onAuthRequired = (): void => {
    if (cleaned || authRequired) return;
    authRequired = true;
    video.removeAttribute("src");
    window.removeEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, onAuthRequired);
    const note = document.createElement("p");
    note.className = "note note--missing";
    note.setAttribute("role", "alert");
    note.textContent =
      "Dashboard pairing expired. Run `octowright dashboard` and open the new URL to resume video.";
    target.append(note);
  };
  if (bearer !== null) {
    window.addEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, onAuthRequired);
  }
  try {
    if (bearer !== null) {
      const configureMediaAuth = options.configureMediaAuth ?? configureDashboardMediaAuth;
      await configureMediaAuth(bearer, {
        ...(options.signal ? { signal: options.signal } : {}),
        onRecovered: () => {
          if (!cleaned && !authRequired && !options.signal?.aborted) video.load();
        },
        onRecoveryFailed: () => handleDashboardUnauthorized(),
        onUnauthorized: () => handleDashboardUnauthorized(),
      });
      if (options.signal?.aborted) {
        clearMediaAuth();
        throw new DOMException("video load aborted", "AbortError");
      }
    }
    if (!authRequired) video.src = path;
    return () => {
      if (cleaned) return;
      cleaned = true;
      window.removeEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, onAuthRequired);
      video.removeAttribute("src");
      if (bearer !== null) clearMediaAuth();
    };
  } catch (error) {
    window.removeEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, onAuthRequired);
    video.removeAttribute("src");
    clearMediaAuth();
    if (options.signal?.aborted) throw error;
    const note = document.createElement("p");
    note.className = "note note--missing";
    note.setAttribute("role", "alert");
    note.textContent = `Video unavailable: ${(error as Error).message}`;
    target.append(note);
    throw error;
  }
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
  dl.className = "btn btn--secondary";
  dl.textContent = "Download .zip";
  dl.setAttribute("download", "");
  bindProtectedDownload(dl, traceDownloadUrl(detail.id), `${safeDownloadName(detail.id)}-trace.zip`, status);
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

export function installDashboardAuthRequiredNotice(root: HTMLElement): () => void {
  const onAuthRequired = (): void => {
    if (root.querySelector('[data-testid="dashboard-auth-required"]')) return;
    const note = document.createElement("p");
    note.className = "note note--missing";
    note.setAttribute("role", "alert");
    note.setAttribute("data-testid", "dashboard-auth-required");
    note.textContent =
      "Dashboard pairing expired. Run `octowright dashboard` and open the new URL to reconnect this session.";
    root.prepend(note);
  };
  window.addEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, onAuthRequired);
  return () => window.removeEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, onAuthRequired);
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
  // Install before tab isolation and the first guarded fetch: both can clear a
  // missing/expired bearer synchronously, and the actionable state must not be
  // lost behind the entrypoint's generic load-error fallback.
  const removeAuthRequiredNotice = installDashboardAuthRequiredNotice(root);
  window.addEventListener("beforeunload", removeAuthRequiredNotice, { once: true });
  await isolateDashboardTabAuth();
  const detail = await getSession(sessionId);
  log.info({
    event: "session_detail_loaded",
    session_id: sessionId,
    kind: detail.kind,
    live: detail.live,
    has_video: Boolean(detail.video_path),
    has_trace: Boolean(detail.trace_path),
  });

  // Registry-driven dispatch for every non-core kind (including terminal,
  // now a plugin kind like any other). A core-reserved kind
  // (CORE_RESERVED_KINDS above) never lives in the plugin registry, so it
  // skips the registry lookup (and its `/api/plugins` round trip) entirely
  // and falls through to the existing browser page below, unchanged. Any
  // other kind either gets its plugin's own renderer or the fallback
  // renderer with a visible reason -- never a blank page.
  if (!CORE_RESERVED_KINDS.has(detail.kind)) {
    const registry = await loadPluginRegistry();
    const chosen = resolveRenderer(registry, detail.kind);
    const { bootStreamSession, importRenderer } = await import("./session-stream.js");
    let mount: MountStream;
    if ("code" in chosen) {
      // No usable renderer for this kind: the fallback, with its reason
      // (no-frontend / version-mismatch).
      mount = (el, ctx) => mountFallbackStream(el, ctx, chosen);
    } else {
      const mod = await importRenderer(chosen.moduleUrl);
      mount =
        "code" in mod
          ? (el: HTMLElement, ctx: StreamContext) => mountFallbackStream(el, ctx, mod)
          : mod.mountStream;
    }
    await bootStreamSession(root, sessionId, detail, mount, {
      ...(opts.webSocketCtor ? { webSocketCtor: opts.webSocketCtor } : {}),
    });
    // Same event name as the core-reserved-kind completion log below, so a
    // query for "did this session finish booting" never silently loses
    // plugin sessions to a differently-named event.
    log.info({ event: "session_boot_complete", session_id: sessionId, kind: detail.kind });
    return;
  }
  // A core-reserved kind: fall through to the existing browser page below, unchanged.

  const refs = buildLayout(root);
  renderHeader(refs.header, detail);
  const videoEl = renderVideo(refs.videoSlot, detail);
  let videoCleanup: (() => void) | null = null;
  let videoAbort: AbortController | null = null;
  let videoDisposed = false;
  if (videoEl && getDashboardBearer() !== null) {
    videoAbort = new AbortController();
    void loadProtectedVideo(refs.videoSlot, videoEl, detail.id, { signal: videoAbort.signal })
      .then((cleanup) => {
        if (videoDisposed) cleanup();
        else videoCleanup = cleanup;
      })
      .catch((error: unknown) => {
        if (!videoAbort?.signal.aborted) {
          log.warn({ event: "session_video_load_failed", session_id: detail.id, error: String(error) });
        }
      });
  }
  const disposeVideo = (): void => {
    videoDisposed = true;
    videoAbort?.abort();
    if (videoCleanup) videoCleanup();
    else clearDashboardMediaAuth();
    videoCleanup = null;
  };
  renderTraceControls(refs.traceSlot, detail);
  renderFooter(refs.footer, detail);

  // Live preview panel: streams /screencast while the session is live; shows a
  // closed-state placeholder otherwise. Stop on page unload to release the socket.
  const livePreview = mountLivePreview(refs.livePreviewSlot, {
    sessionId: detail.id,
    isLive: detail.live,
    fullscreenMode: detail.screencast?.fullscreen_mode ?? "native",
    ...(detail.screencast ? { fps: detail.screencast.fps } : {}),
  });
  livePreview.start();
  window.addEventListener("beforeunload", () => {
    livePreview.destroy();
    disposeVideo();
    disposeScreenshotsPanel(refs.screenshotsPanel);
  });

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
        // transitions to closed mid-connection. Close the live preview
        // screencast immediately so the user sees a closed state instead of a
        // reconnectable stream error against a dead page.
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

export function renderSessionBootError(root: HTMLElement, error: unknown): void {
  if (root.querySelector('[data-testid="dashboard-auth-required"]')) return;
  root.textContent = `Session failed to load: ${(error as Error).message}`;
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
        renderSessionBootError(root, err);
      });
    }
  } else {
    log.warn({ event: "session_root_missing" });
  }
}
