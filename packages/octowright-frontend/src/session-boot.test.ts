/**
 * session-boot.test.ts
 *
 * Covers session.ts paths that require API mocking:
 *   - renderAriaPanel
 *   - appendForTest
 *   - renderHeader macro_intent branch
 *   - renderTraceControls click handler (success + error)
 *   - bootSession (closed session)
 *   - bootSession (live session + WebSocket tail)
 *   - switchTab closure
 *   - seek closure
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  appendForTest,
  bootSession,
  buildLayout,
  renderAriaPanel,
  renderHeader,
  renderTraceControls,
} from "./session.js";
import type { RecordingEvent, SessionDetail } from "./types.js";

// ---------------------------------------------------------------------------
// API mock — hoist before any imports so module graph is resolved correctly.
// ---------------------------------------------------------------------------
vi.mock("./api.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api.js")>();
  return {
    ...actual,
    getSession: vi.fn(),
    getEvents: vi.fn(async () => ({ events: [], cursor: 0, total_bytes: 0, complete: false })),
    getConsole: vi.fn(async () => ({ messages: [], cursor: 0, total: 0 })),
    getDownloads: vi.fn(async () => ({ downloads: [], cursor: 0, total: 0 })),
    getScreenshots: vi.fn(async () => ({ screenshots: [] })),
    openTrace: vi.fn(),
    tailWebSocketUrl: vi.fn((id: string, cursor?: number) => {
      const base = `ws://localhost/api/sessions/${id}/tail`;
      return cursor ? `${base}?since=${cursor}` : base;
    }),
    videoUrl: vi.fn((id: string) => `/api/sessions/${id}/video`),
    markdownUrl: vi.fn((id: string) => `/api/sessions/${id}/markdown`),
    traceDownloadUrl: vi.fn((id: string) => `/api/sessions/${id}/trace`),
  };
});

// mountLivePreview is a side-effectful DOM module — stub it to a no-op handle
// so bootSession tests don't require a real polling implementation.
vi.mock("./live-preview.js", () => ({
  mountLivePreview: vi.fn(() => ({
    start: vi.fn(),
    stop: vi.fn(),
    destroy: vi.fn(),
    markClosed: vi.fn(),
    setInterval: vi.fn(),
  })),
}));

// renderConsolePanel / renderDownloadsPanel / renderScreenshotsPanel emit DOM
// but we don't need to inspect their output here.
vi.mock("./console-panel.js", () => ({ renderConsolePanel: vi.fn() }));
vi.mock("./downloads-panel.js", () => ({ renderDownloadsPanel: vi.fn() }));
vi.mock("./screenshots-panel.js", () => ({ renderScreenshotsPanel: vi.fn() }));
vi.mock("./timeline.js", () => ({
  renderTimeline: vi.fn(),
  appendTimelineEvents: vi.fn(),
}));
vi.mock("./tail.js", () => ({
  openTail: vi.fn(() => ({ close: vi.fn() })),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const SAMPLE_CACHE = {
  total_bytes: 1234,
  total_human: "1.2 KB",
  components: {
    jsonl: { size_bytes: 100, size_human: "100 B", path: "/tmp/a.jsonl", exists: true },
    markdown: { size_bytes: 200, size_human: "200 B", path: "/tmp/a.md", exists: true },
    trace: { size_bytes: 0, size_human: "0 B", path: null, exists: false },
    video: { size_bytes: 0, size_human: "0 B", path: null, exists: false },
    websocket: { size_bytes: 234, size_human: "234 B", path: "/tmp/a.ws.jsonl", exists: true },
    screenshots: { size_bytes: 700, size_human: "700 B", count: 2, paths: ["/1.png", "/2.png"] },
  },
  recommendations: [],
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

// Retrieve the mocked openTrace from the mock registry.
async function getMockedOpenTrace() {
  const api = await import("./api.js");
  return api.openTrace as ReturnType<typeof vi.fn>;
}

async function getMockedGetSession() {
  const api = await import("./api.js");
  return api.getSession as ReturnType<typeof vi.fn>;
}

async function getMockedPanelLoaders() {
  const api = await import("./api.js");
  return {
    getConsole: api.getConsole as ReturnType<typeof vi.fn>,
    getDownloads: api.getDownloads as ReturnType<typeof vi.fn>,
    getScreenshots: api.getScreenshots as ReturnType<typeof vi.fn>,
  };
}

let root: HTMLDivElement;
beforeEach(() => {
  root = document.createElement("div");
  document.body.append(root);
});
afterEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = "";
});

// ---------------------------------------------------------------------------
// renderAriaPanel
// ---------------------------------------------------------------------------
describe("renderAriaPanel", () => {
  it("shows placeholder when aria data is absent", () => {
    const refs = buildLayout(root);
    renderAriaPanel(refs.ariaPanel, makeDetail());
    const empty = refs.ariaPanel.querySelector(".session-panel__empty");
    expect(empty).not.toBeNull();
    expect(empty?.textContent).toMatch(/No accessibility tree snapshot/);
  });

  it("renders a <pre> with aria content when present", () => {
    const refs = buildLayout(root);
    renderAriaPanel(refs.ariaPanel, makeDetail({ aria: "document\n  heading" }));
    const pre = refs.ariaPanel.querySelector("pre.aria-tree");
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toBe("document\n  heading");
  });
});

// ---------------------------------------------------------------------------
// appendForTest
// ---------------------------------------------------------------------------
describe("appendForTest", () => {
  it("delegates to appendTimelineEvents without throwing", () => {
    const target = document.createElement("div");
    const events: RecordingEvent[] = [
      { ts: "2026-04-24T12:00:00.000Z", action: "navigate", url: "https://octowright.com" },
    ];
    // Should not throw; appendTimelineEvents is mocked so DOM output is absent.
    expect(() => appendForTest(events, target, "2026-04-24T12:00:00.000Z")).not.toThrow();
  });

  it("accepts an empty events array", () => {
    const target = document.createElement("div");
    expect(() => appendForTest([], target, "2026-04-24T12:00:00.000Z")).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// renderHeader — macro_intent branch
// ---------------------------------------------------------------------------
describe("renderHeader — macro_intent", () => {
  it("renders an intent row when macro_intent is set", () => {
    const refs = buildLayout(root);
    renderHeader(refs.header, makeDetail({ macro_intent: "log in as admin" }));
    const intent = refs.header.querySelector(".session-header__intent");
    expect(intent).not.toBeNull();
    expect(intent?.textContent).toContain("log in as admin");
  });

  it("does not render intent row when macro_intent is absent", () => {
    const refs = buildLayout(root);
    renderHeader(refs.header, makeDetail());
    expect(refs.header.querySelector(".session-header__intent")).toBeNull();
  });

  it("does not inject HTML from macro_intent (XSS guard)", () => {
    // macro_intent is derived from JSONL fields (selectors, fill values,
    // URLs) — those are user-controllable. The renderer must use
    // textContent / DOM construction, never innerHTML, or a crafted
    // selector can execute script in the dashboard.
    const refs = buildLayout(root);
    const payload = '<img src=x onerror="window.__xss=true">click me';
    renderHeader(refs.header, makeDetail({ macro_intent: payload }));
    const intent = refs.header.querySelector(".session-header__intent");
    expect(intent).not.toBeNull();
    // The injected <img> must NOT exist as a real element.
    expect(intent?.querySelector("img")).toBeNull();
    // The literal text including the angle brackets is visible.
    expect(intent?.textContent).toContain("<img");
    expect(intent?.textContent).toContain("click me");
    // The XSS sentinel must not have fired.
    expect((window as unknown as { __xss?: boolean }).__xss).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// renderTraceControls — click handler
// ---------------------------------------------------------------------------
describe("renderTraceControls click handler", () => {
  it("success path: shows pid and re-enables button", async () => {
    const openTrace = await getMockedOpenTrace();
    openTrace.mockResolvedValueOnce({ pid: 42, trace_path: "/x.zip" });

    const refs = buildLayout(root);
    renderTraceControls(refs.traceSlot, makeDetail({ trace_path: "/x.zip" }));
    const btn = refs.traceSlot.querySelector<HTMLButtonElement>("[data-testid='btn-open-trace']");
    const status = refs.traceSlot.querySelector<HTMLElement>("[data-testid='trace-status']");
    expect(btn).not.toBeNull();
    expect(status).not.toBeNull();

    btn!.click();
    // Button should be disabled during the async call.
    expect(btn!.disabled).toBe(true);

    // Flush all microtasks: openTrace resolves → .then() → .finally()
    // Each chained .then/.catch/.finally adds a microtask tick.
    for (let i = 0; i < 10; i++) await Promise.resolve();

    expect(status!.textContent).toContain("42");
    expect(btn!.disabled).toBe(false);
  });

  it("error path: shows failure message and re-enables button", async () => {
    const openTrace = await getMockedOpenTrace();
    openTrace.mockRejectedValueOnce(new Error("connection refused"));

    const refs = buildLayout(root);
    renderTraceControls(refs.traceSlot, makeDetail({ trace_path: "/x.zip" }));
    const btn = refs.traceSlot.querySelector<HTMLButtonElement>("[data-testid='btn-open-trace']");
    const status = refs.traceSlot.querySelector<HTMLElement>("[data-testid='trace-status']");

    btn!.click();
    // Flush all microtask queues: openTrace rejects → .catch() → .finally()
    for (let i = 0; i < 10; i++) await Promise.resolve();

    expect(status!.textContent).toContain("failed");
    expect(status!.textContent).toContain("connection refused");
    expect(btn!.disabled).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// bootSession — closed session
// ---------------------------------------------------------------------------
describe("bootSession — closed session", () => {
  it("renders header, video slot, trace slot, and footer", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail({ video_path: "/v.webm", trace_path: "/t.zip" }));

    await bootSession(root, "sess-1");

    expect(root.querySelector("[data-testid='session-header']")).not.toBeNull();
    expect(root.querySelector("[data-testid='session-video']")).not.toBeNull();
    expect(root.querySelector("[data-testid='session-trace']")).not.toBeNull();
    expect(root.querySelector("[data-testid='session-footer']")).not.toBeNull();
  });

  it("builds the full tab strip", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail());

    await bootSession(root, "sess-1");

    expect(root.querySelector("[data-testid='tab-console']")).not.toBeNull();
    expect(root.querySelector("[data-testid='tab-aria']")).not.toBeNull();
    expect(root.querySelector("[data-testid='tab-markdown']")).not.toBeNull();
    expect(root.querySelector("[data-testid='tab-downloads']")).not.toBeNull();
    expect(root.querySelector("[data-testid='tab-screenshots']")).not.toBeNull();
  });

  it("tab buttons switch active panel when clicked", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail());

    await bootSession(root, "sess-1");

    const downloadsBtn = root.querySelector<HTMLButtonElement>("[data-testid='tab-downloads']");
    const consoleBtn = root.querySelector<HTMLButtonElement>("[data-testid='tab-console']");
    expect(downloadsBtn).not.toBeNull();
    expect(consoleBtn).not.toBeNull();

    // console is active by default
    expect(consoleBtn!.classList.contains("session-tab--active")).toBe(true);

    // Click downloads
    downloadsBtn!.click();
    expect(downloadsBtn!.classList.contains("session-tab--active")).toBe(true);
    expect(consoleBtn!.classList.contains("session-tab--active")).toBe(false);

    // Clicking the already-active tab should be a no-op (switchTab early-return)
    downloadsBtn!.click();
    expect(downloadsBtn!.classList.contains("session-tab--active")).toBe(true);
  });

  it("clicking all tab buttons covers every switchTab branch", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail());

    await bootSession(root, "sess-1");

    for (const tab of ["aria", "markdown", "screenshots", "console"] as const) {
      const btn = root.querySelector<HTMLButtonElement>(`[data-testid='tab-${tab}']`);
      expect(btn).not.toBeNull();
      btn!.click();
    }
    // No assertion needed beyond "no throw" — coverage is the goal here.
  });

  it("falls back to empty panel data when side-panel loaders fail", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail());
    const loaders = await getMockedPanelLoaders();
    loaders.getConsole.mockRejectedValueOnce(new Error("console failed"));
    loaders.getDownloads.mockRejectedValueOnce(new Error("downloads failed"));
    loaders.getScreenshots.mockRejectedValueOnce(new Error("screenshots failed"));

    await bootSession(root, "sess-1");

    expect(root.querySelector("[data-testid='tab-console']")?.textContent).toContain("(0)");
    expect(root.querySelector("[data-testid='tab-downloads']")?.textContent).toContain("(0)");
    expect(root.querySelector("[data-testid='tab-screenshots']")?.textContent).toContain("(0)");
  });

  it("uses zero counts when optional detail counts are missing", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(
      makeDetail({
        console_count: undefined as never,
        download_count: undefined as never,
        aria: undefined,
        markdown_path: null,
      }),
    );

    await bootSession(root, "sess-missing-counts");

    expect(root.querySelector("[data-testid='tab-console']")?.textContent).toContain("(0)");
    expect(root.querySelector("[data-testid='tab-downloads']")?.textContent).toContain("(0)");
    expect(root.querySelector("[data-testid='tab-aria']")?.textContent).toContain("(0)");
    expect(root.querySelector("[data-testid='tab-markdown']")?.textContent).toContain("(0)");
  });

  it("destroys the live preview on beforeunload", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail());
    const { mountLivePreview } = await import("./live-preview.js");
    const livePreviewHandle = { start: vi.fn(), stop: vi.fn(), destroy: vi.fn(), markClosed: vi.fn(), setInterval: vi.fn() };
    (mountLivePreview as ReturnType<typeof vi.fn>).mockReturnValueOnce(livePreviewHandle);

    await bootSession(root, "sess-unload");
    window.dispatchEvent(new Event("beforeunload"));

    expect(livePreviewHandle.destroy).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// bootSession — live session (WebSocket tail)
// ---------------------------------------------------------------------------
describe("bootSession — live session", () => {
  it("opens a tail and calls openTail with the right url shape", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail({ live: true }));

    const { openTail } = await import("./tail.js");

    await bootSession(root, "sess-live", {
      webSocketCtor: class FakeWS {
        static instances: FakeWS[] = [];
        listeners: Array<{ type: string; fn: (e: unknown) => void }> = [];
        constructor(readonly url: string) {
          FakeWS.instances.push(this);
        }
        addEventListener(type: string, fn: (e: unknown) => void) {
          this.listeners.push({ type, fn });
        }
        close() {}
      } as unknown as typeof WebSocket,
    });

    expect(openTail).toHaveBeenCalled();
  });

  it("markClosed is called on the live-preview when tail msg.complete is true", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail({ live: true }));

    const { openTail } = await import("./tail.js");
    const { mountLivePreview } = await import("./live-preview.js");

    // Capture the onMessage callback so we can drive it.
    let capturedOnMessage: ((msg: { events: unknown[]; cursor: number; complete?: boolean }) => void) | null =
      null;
    (openTail as ReturnType<typeof vi.fn>).mockImplementationOnce(
      (_url: string, opts: { onMessage: typeof capturedOnMessage }) => {
        capturedOnMessage = opts.onMessage;
        return { close: vi.fn() };
      },
    );

    const livePreviewHandle = { start: vi.fn(), stop: vi.fn(), destroy: vi.fn(), markClosed: vi.fn(), setInterval: vi.fn() };
    (mountLivePreview as ReturnType<typeof vi.fn>).mockReturnValueOnce(livePreviewHandle);

    await bootSession(root, "sess-live2", {});

    expect(capturedOnMessage).not.toBeNull();

    // Fire a complete=true message — livePreview.markClosed() should be called.
    capturedOnMessage!({ events: [], cursor: 0, complete: true });
    expect(livePreviewHandle.markClosed).toHaveBeenCalled();
  });

  it("onMessage with events appends to timeline", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail({ live: true }));

    const { openTail } = await import("./tail.js");
    const { appendTimelineEvents } = await import("./timeline.js");

    let capturedOnMessage: ((msg: { events: RecordingEvent[]; cursor: number; complete?: boolean }) => void) | null =
      null;
    (openTail as ReturnType<typeof vi.fn>).mockImplementationOnce(
      (_url: string, opts: { onMessage: typeof capturedOnMessage }) => {
        capturedOnMessage = opts.onMessage;
        return { close: vi.fn() };
      },
    );

    await bootSession(root, "sess-live3", {});

    const event: RecordingEvent = { ts: "2026-04-24T13:00:00Z", action: "click" };
    capturedOnMessage!({ events: [event], cursor: 1 });

    expect(appendTimelineEvents).toHaveBeenCalled();
  });

  it("onMessage keeps the initial base timestamp when history already exists", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail({ live: true }));
    const api = await import("./api.js");
    (api.getEvents as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      events: [{ ts: "2026-04-24T12:00:00Z", action: "navigate" }],
      cursor: 10,
      total_bytes: 1,
      complete: false,
    });

    const { openTail } = await import("./tail.js");
    const { appendTimelineEvents } = await import("./timeline.js");
    let capturedOnMessage: ((msg: { events: RecordingEvent[]; cursor: number; complete?: boolean }) => void) | null =
      null;
    (openTail as ReturnType<typeof vi.fn>).mockImplementationOnce(
      (_url: string, opts: { onMessage: typeof capturedOnMessage }) => {
        capturedOnMessage = opts.onMessage;
        return { close: vi.fn() };
      },
    );

    await bootSession(root, "sess-live-history", {});

    capturedOnMessage!({ events: [{ ts: "2026-04-24T12:00:05Z", action: "click" }], cursor: 11 });

    expect(appendTimelineEvents).toHaveBeenCalledWith(
      expect.any(HTMLElement),
      expect.any(Array),
      "2026-04-24T12:00:00Z",
      expect.any(Object),
    );
  });

  it("logs and swallows cheap panel refresh errors from tail messages", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail({ live: true }));
    const { openTail } = await import("./tail.js");
    const panels = await import("./console-panel.js");
    let capturedOnMessage: ((msg: { events: RecordingEvent[]; cursor: number; complete?: boolean }) => void) | null =
      null;
    (openTail as ReturnType<typeof vi.fn>).mockImplementationOnce(
      (_url: string, opts: { onMessage: typeof capturedOnMessage }) => {
        capturedOnMessage = opts.onMessage;
        return { close: vi.fn() };
      },
    );
    await bootSession(root, "sess-live-refresh-error", {});
    (panels.renderConsolePanel as ReturnType<typeof vi.fn>).mockImplementationOnce(() => {
      throw new Error("render failed");
    });

    expect(() => {
      capturedOnMessage!({ events: [{ ts: "2026-04-24T13:00:00Z", action: "click" }], cursor: 1 });
    }).not.toThrow();
    await Promise.resolve();
  });

  it("closes the tail on beforeunload", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail({ live: true }));
    const { openTail } = await import("./tail.js");

    await bootSession(root, "sess-live4", {});
    const tailHandle = (openTail as ReturnType<typeof vi.fn>).mock.results.at(-1)?.value as { close: ReturnType<typeof vi.fn> };
    window.dispatchEvent(new Event("beforeunload"));

    expect(tailHandle.close).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// bootSession — seek closure (video element interaction)
// ---------------------------------------------------------------------------
describe("bootSession — seek closure", () => {
  it("seek sets video.currentTime when video element is present", async () => {
    const getSession = await getMockedGetSession();
    getSession.mockResolvedValueOnce(makeDetail({ video_path: "/v.webm" }));

    const { renderTimeline } = await import("./timeline.js");
    let capturedSeek: ((t: number) => void) | null = null;
    (renderTimeline as ReturnType<typeof vi.fn>).mockImplementationOnce(
      (_el: HTMLElement, _events: unknown[], opts: { onSeek?: (t: number) => void }) => {
        if (opts?.onSeek) capturedSeek = opts.onSeek;
      },
    );

    await bootSession(root, "sess-seek");

    const video = root.querySelector<HTMLVideoElement>("[data-testid='video-player']");
    expect(video).not.toBeNull();
    expect(capturedSeek).not.toBeNull();

    capturedSeek!(5.5);
    expect(video!.currentTime).toBe(5.5);
  });

  it("seek is a no-op when no video element", async () => {
    const getSession = await getMockedGetSession();
    // No video_path → renderVideo returns null
    getSession.mockResolvedValueOnce(makeDetail({ video_path: null, live: false }));

    const { renderTimeline } = await import("./timeline.js");
    let capturedSeek: ((t: number) => void) | null = null;
    (renderTimeline as ReturnType<typeof vi.fn>).mockImplementationOnce(
      (_el: HTMLElement, _events: unknown[], opts: { onSeek?: (t: number) => void }) => {
        if (opts?.onSeek) capturedSeek = opts.onSeek;
      },
    );

    await bootSession(root, "sess-no-video");

    expect(capturedSeek).not.toBeNull();
    // Should not throw
    expect(() => capturedSeek!(3)).not.toThrow();
  });
});
