import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getConsole: vi.fn(async () => ({ messages: [], cursor: 0, total: 0 })),
  getDownloads: vi.fn(async () => ({ downloads: [], cursor: 0, total: 0 })),
  getEvents: vi.fn(async () => ({ events: [], cursor: 0, total_bytes: 0, complete: false })),
  getScreenshots: vi.fn(async () => ({ screenshots: [] })),
  getSession: vi.fn(),
  markdownUrl: vi.fn((id: string) => `/api/sessions/${id}/markdown`),
  openTrace: vi.fn(),
  tailWebSocketUrl: vi.fn((id: string) => `ws://localhost/api/sessions/${id}/tail`),
  traceDownloadUrl: vi.fn((id: string) => `/api/sessions/${id}/trace`),
  videoUrl: vi.fn((id: string) => `/api/sessions/${id}/video`),
}));

vi.mock("./api.js", () => apiMocks);
vi.mock("./live-preview.js", () => ({
  mountLivePreview: vi.fn(() => ({
    destroy: vi.fn(),
    markClosed: vi.fn(),
    setInterval: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
  })),
}));
vi.mock("./tail.js", () => ({ openTail: vi.fn(() => ({ close: vi.fn() })) }));

beforeEach(() => {
  vi.resetModules();
  document.body.innerHTML = "";
  window.history.pushState({}, "", "/");
});

afterEach(() => {
  document.body.innerHTML = "";
});

describe("session module entrypoint", () => {
  it("renders invalid URL text when #app exists without a session id", async () => {
    document.body.innerHTML = '<div id="app"></div>';
    window.history.pushState({}, "", "/not-a-session");

    await import("./session.js");

    expect(document.getElementById("app")?.textContent).toBe("Invalid session URL");
  });

  it("handles a missing #app root at import time", async () => {
    await expect(import("./session.js")).resolves.toBeDefined();
  });

  it("renders boot failure text when automatic boot rejects", async () => {
    document.body.innerHTML = '<div id="app"></div>';
    window.history.pushState({}, "", "/sessions/s1");
    apiMocks.getSession.mockRejectedValueOnce(new Error("missing"));

    await import("./session.js");
    for (let i = 0; i < 8; i++) await Promise.resolve();

    expect(document.getElementById("app")?.textContent).toContain("Session failed to load: missing");
  });
});
