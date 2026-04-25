import { beforeEach, describe, expect, it } from "vitest";
import {
  buildLayout,
  renderFooter,
  renderHeader,
  renderTraceControls,
  renderVideo,
  sessionIdFromPath,
  setActiveTab,
} from "./session.js";
import type { SessionDetail } from "./types.js";

function makeDetail(overrides: Partial<SessionDetail> = {}): SessionDetail {
  return {
    id: "sess-1",
    kind: "chromium",
    label: "demo",
    profile: null,
    url: "https://example.com",
    started_at: "2026-04-24T12:00:00.000Z",
    live: false,
    log_path: "/tmp/x.jsonl",
    video_path: null,
    trace_path: null,
    action_count: 0,
    console_count: 0,
    download_count: 0,
    page_count: 1,
    title: null,
    ...overrides,
  };
}

let root: HTMLDivElement;
beforeEach(() => {
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
    expect(root.querySelector('[data-testid="tab-console"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="tab-downloads"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="tab-screenshots"]')).not.toBeNull();
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
