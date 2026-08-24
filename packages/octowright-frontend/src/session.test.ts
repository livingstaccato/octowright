import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the terminal-boot module so we can assert delegation, and override only
// getSession on the real api module (so render-function tests keep videoUrl
// etc.). vi.hoisted lets the spies exist before the hoisted vi.mock factories.
const { bootTerminalSessionMock } = vi.hoisted(() => ({ bootTerminalSessionMock: vi.fn() }));
const { getSessionMock, getEventsMock } = vi.hoisted(() => ({
  getSessionMock: vi.fn(),
  getEventsMock: vi.fn(),
}));
// The registry-driven dispatch (Task 7) dynamically imports "./session-stream.js"
// for a non-browser, non-terminal kind. Mocked here so the dispatch tests can
// assert what session.ts hands it -- the resolved mount function and the
// boot arguments -- without depending on session-stream.ts's own internals,
// which are covered by session-stream.test.ts.
const { bootStreamSessionMock, importRendererMock } = vi.hoisted(() => ({
  bootStreamSessionMock: vi.fn(async () => undefined),
  importRendererMock: vi.fn(),
}));
vi.mock("./session-terminal.js", () => ({
  bootTerminalSession: bootTerminalSessionMock,
  buildTerminalLayout: () => ({}),
}));
vi.mock("./session-stream.js", () => ({
  bootStreamSession: bootStreamSessionMock,
  importRenderer: importRendererMock,
}));
vi.mock("./api.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api.js")>()),
  getSession: getSessionMock,
  getEvents: getEventsMock,
}));

import { getDashboardBearer, setDashboardBearer } from "./dashboard-auth.js";
import { RENDERER_API_VERSION } from "./plugin-registry.js";
import {
  bootSession,
  buildLayout,
  loadProtectedVideo,
  installDashboardAuthRequiredNotice,
  renderCachePanel,
  renderFooter,
  renderHeader,
  renderMarkdownPanel,
  renderSessionBootError,
  renderTraceControls,
  renderVideo,
  sessionIdFromPath,
  setActiveTab,
} from "./session.js";
import type { SessionDetail } from "./types.js";

const SAMPLE_CACHE = {
  total_bytes: 1234,
  total_human: "1.2 KB",
  components: {
    jsonl: { size_bytes: 100, size_human: "100 B", path: "/tmp/a.jsonl", exists: true },
    markdown: { size_bytes: 200, size_human: "200 B", path: "/tmp/a.markdown.md", exists: true },
    trace: { size_bytes: 0, size_human: "0 B", path: null, exists: false },
    video: { size_bytes: 0, size_human: "0 B", path: null, exists: false },
    websocket: { size_bytes: 234, size_human: "234 B", path: "/tmp/a.websocket.jsonl", exists: true },
    screenshots: {
      size_bytes: 700,
      size_human: "700 B",
      count: 2,
      paths: ["/tmp/1.png", "/tmp/2.png"],
    },
  },
  recommendations: ["Enable compression on recordings."],
};

function makeDetail(overrides: Partial<SessionDetail> = {}): SessionDetail {
  return {
    id: "sess-1",
    kind: "chromium",
    label: "demo",
    profile: null,
    url: "https://octowright.com",
    started_at: "2026-04-24T12:00:00.000Z",
    live: false,
    log_path: "/tmp/x.jsonl",
    video_path: null,
    trace_path: null,
    markdown_path: null,
    action_count: 0,
    console_count: 0,
    download_count: 0,
    page_count: 1,
    websocket_path: null,
    cache: SAMPLE_CACHE,
    title: null,
    ...overrides,
  };
}

let root: HTMLDivElement;
beforeEach(() => {
  sessionStorage.clear();
  root = document.createElement("div");
  document.body.append(root);
});

describe("sessionIdFromPath", () => {
  it("extracts session id", () => {
    expect(sessionIdFromPath("/sessions/abc")).toBe("abc");
  });
  it("decodes URL components", () => {
    expect(sessionIdFromPath("/sessions/foo%2Fbar")).toBe("foo/bar");
  });
  it("returns the raw id when URL decoding fails", () => {
    expect(sessionIdFromPath("/sessions/%E0%A4%A")).toBe("%E0%A4%A");
  });
  it("returns null for non-matching paths", () => {
    expect(sessionIdFromPath("/dashboard")).toBeNull();
    expect(sessionIdFromPath("/sessions/")).toBeNull();
  });
});

describe("buildLayout", () => {
  it("creates the expected slots", () => {
    const refs = buildLayout(root);
    expect(refs.header).toBeDefined();
    expect(refs.videoSlot).toBeDefined();
    expect(refs.timeline).toBeDefined();
    expect(root.querySelector('[data-testid="session-left"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="session-right"]')).not.toBeNull();
  });

  it("creates tab strip + three side panels", () => {
    const refs = buildLayout(root);
    expect(refs.tabs).toBeDefined();
    expect(refs.consolePanel.id).toBe("console-panel");
    expect(refs.downloadsPanel.id).toBe("downloads-panel");
    expect(refs.screenshotsPanel.id).toBe("screenshots-panel");
    expect(refs.markdownPanel.id).toBe("markdown-panel");
    expect(root.querySelector('[data-testid="tab-console"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="tab-downloads"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="tab-screenshots"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="tab-markdown"]')).not.toBeNull();
  });

  it("creates a live-preview slot above the timeline", () => {
    const refs = buildLayout(root);
    expect(refs.livePreviewSlot).toBeDefined();
    expect(refs.livePreviewSlot.id).toBe("live-preview-panel");
    // Slot must come before the timeline within the right column.
    const right = root.querySelector('[data-testid="session-right"]');
    expect(right).not.toBeNull();
    const children = Array.from(right?.children ?? []);
    const previewIdx = children.indexOf(refs.livePreviewSlot);
    const timelineIdx = children.indexOf(refs.timeline);
    expect(previewIdx).toBeGreaterThanOrEqual(0);
    expect(timelineIdx).toBeGreaterThanOrEqual(0);
    expect(previewIdx).toBeLessThan(timelineIdx);
  });
});

describe("installDashboardAuthRequiredNotice", () => {
  it("renders one actionable re-pair alert for a stream expiry", () => {
    const dispose = installDashboardAuthRequiredNotice(root);
    window.dispatchEvent(new Event("octowright:dashboard-auth-required"));
    window.dispatchEvent(new Event("octowright:dashboard-auth-required"));
    expect(root.querySelectorAll('[data-testid="dashboard-auth-required"]')).toHaveLength(1);
    expect(root.textContent).toContain("octowright dashboard");
    dispose();
  });

  it("preserves re-pair guidance instead of replacing it with a generic boot error", () => {
    installDashboardAuthRequiredNotice(root);
    window.dispatchEvent(new Event("octowright:dashboard-auth-required"));
    renderSessionBootError(root, new Error("401"));
    expect(root.textContent).toContain("octowright dashboard");
    expect(root.textContent).not.toContain("Session failed to load");
  });
});

describe("setActiveTab", () => {
  it("toggles active class + visibility across the three panels", () => {
    const refs = buildLayout(root);
    setActiveTab(refs, "console");
    expect(refs.consoleTabBtn.classList.contains("session-tab--active")).toBe(true);
    expect(refs.downloadsTabBtn.classList.contains("session-tab--active")).toBe(false);
    expect(refs.consolePanel.style.display).toBe("");
    expect(refs.downloadsPanel.style.display).toBe("none");
    expect(refs.screenshotsPanel.style.display).toBe("none");

    setActiveTab(refs, "screenshots");
    expect(refs.screenshotsTabBtn.classList.contains("session-tab--active")).toBe(true);
    expect(refs.consolePanel.style.display).toBe("none");
    expect(refs.screenshotsPanel.style.display).toBe("");
    expect(refs.screenshotsTabBtn.getAttribute("aria-selected")).toBe("true");
    expect(refs.consoleTabBtn.getAttribute("aria-selected")).toBe("false");
  });
});

describe("renderHeader", () => {
  it("renders title, status, kind icon", () => {
    const refs = buildLayout(root);
    renderHeader(refs.header, makeDetail({ live: true }));
    expect(refs.header.querySelector(".kind-icon--chromium")).not.toBeNull();
    expect(refs.header.querySelector(".status--live")?.textContent).toBe("LIVE");
  });
  it("falls back to id when no label/profile", () => {
    const refs = buildLayout(root);
    renderHeader(refs.header, makeDetail({ label: null, profile: null }));
    expect(refs.header.querySelector(".session-header__title")?.textContent).toBe("sess-1");
  });
});

describe("renderVideo", () => {
  it("shows pending note when live without video", () => {
    const refs = buildLayout(root);
    const v = renderVideo(refs.videoSlot, makeDetail({ live: true, video_path: null }));
    expect(v).toBeNull();
    expect(refs.videoSlot.textContent).toMatch(/will be available/);
  });
  it("shows missing note when closed without video", () => {
    const refs = buildLayout(root);
    renderVideo(refs.videoSlot, makeDetail({ live: false, video_path: null }));
    expect(refs.videoSlot.textContent).toMatch(/no video/);
  });
  it("renders video element when path present", () => {
    const refs = buildLayout(root);
    const v = renderVideo(refs.videoSlot, makeDetail({ video_path: "/x.webm" }));
    expect(v).not.toBeNull();
    expect(v?.src).toContain("/api/sessions/sess-1/video");
  });
  it("does not expose the protected video URL when a dashboard bearer is active", () => {
    setDashboardBearer({ bearer: "video-secret", expires_at: Date.now() / 1000 + 60 });
    const refs = buildLayout(root);
    const video = renderVideo(refs.videoSlot, makeDetail({ video_path: "/x.webm" }));
    expect(video?.src).toBe("");
  });
  it("loads paired video through the normal URL after worker auth without buffering a blob", async () => {
    setDashboardBearer({ bearer: "video-secret", expires_at: Date.now() / 1000 + 60 });
    const refs = buildLayout(root);
    const video = renderVideo(refs.videoSlot, makeDetail({ video_path: "/x.webm" }));
    if (!video) throw new Error("video missing");
    const configureMediaAuth = vi.fn(async () => undefined);
    const clearMediaAuth = vi.fn();
    const fetchFn = vi.fn(() => {
      throw new Error("video must not be fetched into page memory");
    });
    vi.stubGlobal("fetch", fetchFn);
    const blob = vi.spyOn(Response.prototype, "blob");

    const cleanup = await loadProtectedVideo(refs.videoSlot, video, "sess-1", {
      configureMediaAuth,
      clearMediaAuth,
    });

    expect(configureMediaAuth).toHaveBeenCalledWith("video-secret", expect.any(Object));
    expect(video.src).toContain("/api/sessions/sess-1/video");
    expect(fetchFn).not.toHaveBeenCalled();
    expect(blob).not.toHaveBeenCalled();
    cleanup();
    expect(video.hasAttribute("src")).toBe(false);
    expect(clearMediaAuth).toHaveBeenCalledOnce();
  });
  it("reloads native video after the worker restores this page's lost authorization", async () => {
    setDashboardBearer({ bearer: "video-secret", expires_at: Date.now() / 1000 + 60 });
    const refs = buildLayout(root);
    const video = renderVideo(refs.videoSlot, makeDetail({ video_path: "/x.webm" }));
    if (!video) throw new Error("video missing");
    const load = vi.spyOn(video, "load").mockImplementation(() => undefined);
    let onRecovered: (() => void) | undefined;
    const configureMediaAuth = vi.fn(
      async (_bearer: string, options: { onRecovered?: () => void }) => {
        onRecovered = options.onRecovered;
      },
    );

    const cleanup = await loadProtectedVideo(refs.videoSlot, video, "sess-1", {
      configureMediaAuth: configureMediaAuth as never,
    });
    expect(onRecovered).toBeTypeOf("function");

    onRecovered?.();

    expect(load).toHaveBeenCalledOnce();
    expect(getDashboardBearer()).toBe("video-secret");
    cleanup();
  });
  it.each([401, 403])("clears paired video and shows terminal re-pair UX after native status %s", async (status) => {
    setDashboardBearer({ bearer: "expired-secret", expires_at: Date.now() / 1000 + 60 });
    const refs = buildLayout(root);
    const video = renderVideo(refs.videoSlot, makeDetail({ video_path: "/x.webm" }));
    if (!video) throw new Error("video missing");
    let onUnauthorized: ((status: 401 | 403) => void) | undefined;
    const configureMediaAuth = vi.fn(
      async (_bearer: string, options: { onUnauthorized?: (status: 401 | 403) => void }) => {
        onUnauthorized = options.onUnauthorized;
      },
    );
    const clearMediaAuth = vi.fn();
    const cleanup = await loadProtectedVideo(refs.videoSlot, video, "sess-1", {
      configureMediaAuth: configureMediaAuth as never,
      clearMediaAuth,
    });
    expect(onUnauthorized).toBeTypeOf("function");

    onUnauthorized?.(status as 401 | 403);
    onUnauthorized?.(status as 401 | 403);

    expect(getDashboardBearer()).toBeNull();
    expect(video.hasAttribute("src")).toBe(false);
    expect(configureMediaAuth).toHaveBeenCalledOnce();
    const alert = refs.videoSlot.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("Dashboard pairing expired");
    expect(alert?.textContent).toContain("octowright dashboard");
    expect(refs.videoSlot.querySelectorAll('[role="alert"]')).toHaveLength(1);
    cleanup();
    expect(clearMediaAuth).toHaveBeenCalledOnce();
  });
  it("renders an accessible bounded error when worker control is unavailable", async () => {
    setDashboardBearer({ bearer: "video-secret", expires_at: Date.now() / 1000 + 60 });
    const refs = buildLayout(root);
    const video = renderVideo(refs.videoSlot, makeDetail({ video_path: "/x.webm" }));
    if (!video) throw new Error("video missing");
    const configureMediaAuth = vi.fn(async () => {
      throw new Error("service worker could not take control");
    });
    await expect(loadProtectedVideo(refs.videoSlot, video, "sess-1", { configureMediaAuth })).rejects.toThrow();
    expect(video.src).toBe("");
    expect(refs.videoSlot.querySelector('[role="alert"]')?.textContent).toContain("Video unavailable");
  });
});

describe("renderTraceControls", () => {
  it("shows note when no trace", () => {
    const refs = buildLayout(root);
    renderTraceControls(refs.traceSlot, makeDetail({ trace_path: null }));
    expect(refs.traceSlot.querySelector("button")).toBeNull();
  });
  it("shows open button + download link when trace present", () => {
    const refs = buildLayout(root);
    renderTraceControls(refs.traceSlot, makeDetail({ trace_path: "/x.zip" }));
    expect(refs.traceSlot.querySelector("button[data-testid='btn-open-trace']")).not.toBeNull();
    expect(refs.traceSlot.querySelector("a[download]")).not.toBeNull();
  });
});

describe("renderMarkdownPanel", () => {
  it("shows placeholder when markdown path is missing", () => {
    const refs = buildLayout(root);
    renderMarkdownPanel(refs.markdownPanel, makeDetail({ markdown_path: null }));
    const details = refs.markdownPanel.querySelector("details");
    expect(details?.open).toBe(false);
    expect(refs.markdownPanel.textContent).toMatch(/No markdown snapshot/);
  });

  it("shows markdown download link when available", () => {
    const refs = buildLayout(root);
    renderMarkdownPanel(refs.markdownPanel, makeDetail({ markdown_path: "/tmp/session.md" }));
    const link = refs.markdownPanel.querySelector("a[download]");
    expect(link?.getAttribute("href")).toBe("/api/sessions/sess-1/markdown");
  });

  it("does not expose a protected markdown URL to direct navigation", () => {
    setDashboardBearer({ bearer: "markdown-secret", expires_at: Date.now() / 1000 + 60 });
    const refs = buildLayout(root);
    renderMarkdownPanel(refs.markdownPanel, makeDetail({ markdown_path: "/tmp/session.md" }));
    expect(refs.markdownPanel.querySelector("a[download]")?.getAttribute("href")).toBe("#");
  });
});

describe("renderCachePanel", () => {
  it("renders a collapsed cache summary", () => {
    const refs = buildLayout(root);
    renderCachePanel(refs.cachePanel, makeDetail());
    expect(refs.cachePanel.querySelector("summary")?.textContent).toBe("Cache summary (1.2 KB)");
  });

  it("shows recommendations when provided", () => {
    const refs = buildLayout(root);
    renderCachePanel(refs.cachePanel, makeDetail());
    expect(refs.cachePanel.textContent).toContain("Enable compression on recordings.");
  });

  it("omits recommendations block when none are provided", () => {
    const refs = buildLayout(root);
    renderCachePanel(refs.cachePanel, makeDetail({ cache: { ...SAMPLE_CACHE, recommendations: [] } }));
    expect(refs.cachePanel.textContent).not.toContain("Recommendations:");
  });
});

describe("renderFooter", () => {
  it("shows refresh hint for live", () => {
    const refs = buildLayout(root);
    renderFooter(refs.footer, makeDetail({ live: true }));
    expect(refs.footer.textContent).toMatch(/Refreshing/);
  });
  it("shows closed time for closed", () => {
    const refs = buildLayout(root);
    renderFooter(refs.footer, makeDetail({ live: false }));
    expect(refs.footer.textContent).toMatch(/Closed/);
  });
});

describe("bootSession terminal branch", () => {
  beforeEach(() => {
    bootTerminalSessionMock.mockReset();
    bootTerminalSessionMock.mockResolvedValue(undefined);
    // Empty history so the browser-path negative test completes without a
    // real network fetch (the terminal path returns early before getEvents).
    getEventsMock.mockResolvedValue({ events: [], cursor: 0, total_bytes: 0, complete: true });
  });

  it("delegates to bootTerminalSession when kind is terminal", async () => {
    getSessionMock.mockResolvedValue(makeDetail({ kind: "terminal", live: false }));
    const el = document.createElement("div");
    await bootSession(el, "term-0");
    expect(bootTerminalSessionMock).toHaveBeenCalledTimes(1);
    expect(bootTerminalSessionMock.mock.calls[0]?.[1]).toBe("term-0");
  });

  it("does NOT delegate for browser sessions", async () => {
    getSessionMock.mockResolvedValue(makeDetail({ kind: "chromium", live: false }));
    const el = document.createElement("div");
    await bootSession(el, "sess-1");
    expect(bootTerminalSessionMock).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// bootSession — registry-driven dispatch (Task 7)
//
// resolveRenderer's own decision (registered/mismatched/unknown kind -> a
// moduleUrl or a FallbackReason) is already exercised end-to-end in
// plugin-registry.test.ts, so it is not re-tested here under a different
// name. These tests cover what session.ts itself decides on top of that:
// which mount function it hands to bootStreamSession, and that a core
// browser kind never touches the registry at all.
// ---------------------------------------------------------------------------
describe("bootSession — plugin registry dispatch", () => {
  function fetchReturningPlugins(body: Record<string, unknown>) {
    return vi.fn(async (url: string) => {
      if (url === "/api/plugins") {
        return { ok: true, json: async () => body } as Response;
      }
      throw new Error(`unexpected fetch in dispatch test: ${String(url)}`);
    });
  }

  function capturedMount() {
    return bootStreamSessionMock.mock.calls.at(-1)?.[3] as (
      el: HTMLElement,
      ctx: { sessionId: string; live: boolean; kind: string },
    ) => { feed: (events: unknown[]) => void; destroy: () => void };
  }

  beforeEach(() => {
    bootStreamSessionMock.mockReset();
    bootStreamSessionMock.mockResolvedValue(undefined);
    importRendererMock.mockReset();
    getEventsMock.mockResolvedValue({ events: [], cursor: 0, total_bytes: 0, complete: true });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("imports the advertised module and boots the stream page for a registered kind", async () => {
    vi.stubGlobal(
      "fetch",
      fetchReturningPlugins({
        refkind: {
          moduleUrl: "/plugins/p/renderer.js",
          rendererApiVersion: RENDERER_API_VERSION,
          displayName: "Ref",
          layout: "stream",
        },
      }),
    );
    const mountStream = vi.fn();
    importRendererMock.mockResolvedValueOnce({ mountStream });
    getSessionMock.mockResolvedValue(makeDetail({ kind: "refkind", live: false }));

    const el = document.createElement("div");
    await bootSession(el, "ref-0");

    expect(importRendererMock).toHaveBeenCalledWith("/plugins/p/renderer.js");
    expect(bootStreamSessionMock).toHaveBeenCalledTimes(1);
    const [, sessionId, detail, mount] = bootStreamSessionMock.mock.calls[0] as [
      HTMLElement,
      string,
      SessionDetail,
      unknown,
    ];
    expect(sessionId).toBe("ref-0");
    expect(detail.kind).toBe("refkind");
    expect(mount).toBe(mountStream);
  });

  it("falls back with the import-failed reason when the plugin's module fails to load", async () => {
    vi.stubGlobal(
      "fetch",
      fetchReturningPlugins({
        refkind: {
          moduleUrl: "/plugins/p/renderer.js",
          rendererApiVersion: RENDERER_API_VERSION,
          displayName: "Ref",
          layout: "stream",
        },
      }),
    );
    importRendererMock.mockResolvedValueOnce({ code: "import-failed", detail: "404" });
    getSessionMock.mockResolvedValue(makeDetail({ kind: "refkind", live: false }));

    const el = document.createElement("div");
    await bootSession(el, "ref-1");

    expect(bootStreamSessionMock).toHaveBeenCalledTimes(1);
    const mountEl = document.createElement("div");
    capturedMount()(mountEl, { sessionId: "ref-1", live: false, kind: "refkind" });
    const notice = mountEl.querySelector('[data-testid="stream-fallback-notice"]');
    expect(notice?.getAttribute("data-fallback-code")).toBe("import-failed");
  });

  it("falls back with the no-frontend reason for an unregistered non-browser kind", async () => {
    vi.stubGlobal("fetch", fetchReturningPlugins({}));
    getSessionMock.mockResolvedValue(makeDetail({ kind: "unregisteredkind", live: false }));

    const el = document.createElement("div");
    await bootSession(el, "un-0");

    expect(importRendererMock).not.toHaveBeenCalled();
    expect(bootStreamSessionMock).toHaveBeenCalledTimes(1);
    const mountEl = document.createElement("div");
    capturedMount()(mountEl, { sessionId: "un-0", live: false, kind: "unregisteredkind" });
    const notice = mountEl.querySelector('[data-testid="stream-fallback-notice"]');
    expect(notice?.getAttribute("data-fallback-code")).toBe("no-frontend");
  });

  it("never touches the plugin registry for a browser kind", async () => {
    // A spy, not a stub: this must not disturb the browser page's own real
    // network calls (getConsole/getDownloads/getScreenshots), only observe
    // whether any of them targeted /api/plugins.
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    getSessionMock.mockResolvedValue(makeDetail({ kind: "chromium", live: false }));

    const el = document.createElement("div");
    await bootSession(el, "sess-1");

    const urls = fetchSpy.mock.calls.map((call) => String(call[0]));
    expect(urls).not.toContain("/api/plugins");
    expect(bootStreamSessionMock).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});
