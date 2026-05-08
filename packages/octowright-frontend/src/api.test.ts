import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  fetchJson,
  frameUrl,
  getConsole,
  getDownloads,
  getEvents,
  getHealth,
  getMacro,
  getMacros,
  getMacroRepairPreview,
  validateMacro,
  updateMacro,
  validateSessionSelector,
  getPersonas,
  getScenarios,
  getScreenshots,
  getSession,
  getSessions,
  dashboardEventsUrl,
  openTrace,
  markdownUrl,
  screenshotUrl,
  tailWebSocketUrl,
  traceDownloadUrl,
  videoUrl,
} from "./api.js";

interface MockCall {
  url: string;
  init: RequestInit | undefined;
}

function installFetch(payload: unknown, status = 200): MockCall[] {
  const calls: MockCall[] = [];
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: status === 200 ? "OK" : "Bad",
      json: async () => payload,
    } as unknown as Response;
  });
  globalThis.fetch = mock as unknown as typeof fetch;
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchJson", () => {
  it("issues GET by default and parses JSON", async () => {
    const calls = installFetch({ ok: true });
    const result = await fetchJson<{ ok: boolean }>("/api/test");
    expect(result.ok).toBe(true);
    expect(calls[0]?.url).toBe("/api/test");
    expect(calls[0]?.init?.method).toBe("GET");
  });
  it("encodes JSON body on POST", async () => {
    const calls = installFetch({ pid: 7, trace_path: "x" });
    await fetchJson("/api/x", { method: "POST", body: { foo: 1 } });
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.init?.body).toBe(JSON.stringify({ foo: 1 }));
  });
  it("throws ApiError on non-2xx", async () => {
    installFetch({}, 500);
    await expect(fetchJson("/api/x")).rejects.toBeInstanceOf(ApiError);
  });
  it("includes server error details on non-2xx JSON responses", async () => {
    installFetch({ error: "invalid YAML: broken" }, 400);
    await expect(fetchJson("/api/x")).rejects.toThrow("invalid YAML: broken");
  });
  it("forwards AbortSignal", async () => {
    const calls = installFetch({});
    const ac = new AbortController();
    await fetchJson("/api/x", { signal: ac.signal });
    expect(calls[0]?.init?.signal).toBe(ac.signal);
  });
});

describe("typed wrappers", () => {
  beforeEach(() => {
    installFetch({ live: [], closed: [] });
  });

  it("getSessions hits /api/sessions", async () => {
    const calls = installFetch({ live: [], closed: [] });
    await getSessions();
    expect(calls[0]?.url).toBe("/api/sessions");
  });
  it("getSession encodes id", async () => {
    const calls = installFetch({});
    await getSession("a/b");
    expect(calls[0]?.url).toBe("/api/sessions/a%2Fb");
  });
  it("getEvents includes since", async () => {
    const calls = installFetch({ events: [], cursor: 0, total_bytes: 0, complete: false });
    await getEvents("xyz", 42);
    expect(calls[0]?.url).toBe("/api/sessions/xyz/events?since=42");
  });
  it("getScenarios", async () => {
    const calls = installFetch({ live: [] });
    await getScenarios();
    expect(calls[0]?.url).toBe("/api/scenarios");
  });
  it("getPersonas", async () => {
    const calls = installFetch([]);
    await getPersonas();
    expect(calls[0]?.url).toBe("/api/personas");
  });
  it("getMacros", async () => {
    const calls = installFetch([]);
    await getMacros();
    expect(calls[0]?.url).toBe("/api/macros");
  });
  it("getMacroRepairPreview encodes macro name", async () => {
    const calls = installFetch({ macro: "login/test", suggestions: [] });
    await getMacroRepairPreview("login/test");
    expect(calls[0]?.url).toBe("/api/macros/login%2Ftest/repair_preview");
  });
  it("getMacro encodes slash names", async () => {
    const calls = installFetch({ name: "login/test" });
    await getMacro("login/test");
    expect(calls[0]?.url).toBe("/api/macros/login%2Ftest");
  });
  it("validateMacro posts JSON body", async () => {
    const calls = installFetch({ ok: true, valid: true, issues: [] });
    const macro = { name: "login", actions: [] };
    await validateMacro("login", macro);
    expect(calls[0]?.url).toBe("/api/macros/login/validate");
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.init?.body).toBe(JSON.stringify({ macro }));
  });
  it("updateMacro puts payload", async () => {
    const calls = installFetch({ ok: true, name: "login" });
    const macro = { name: "login", actions: [] };
    await updateMacro("login", macro);
    expect(calls[0]?.url).toBe("/api/macros/login");
    expect(calls[0]?.init?.method).toBe("PUT");
    expect(calls[0]?.init?.body).toBe(JSON.stringify({ macro }));
  });
  it("validateSessionSelector encodes session id", async () => {
    const calls = installFetch({ ok: true, present: true, selector: "#x", session_id: "s/1" });
    await validateSessionSelector("s/1", "#x");
    expect(calls[0]?.url).toBe("/api/sessions/s%2F1/selector/validate");
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.init?.body).toBe(JSON.stringify({ selector: "#x" }));
  });
  it("getScreenshots", async () => {
    const calls = installFetch({ screenshots: [] });
    await getScreenshots("s1");
    expect(calls[0]?.url).toBe("/api/sessions/s1/screenshots");
  });
  it("getConsole defaults to since=0 and omits level", async () => {
    const calls = installFetch({ messages: [], cursor: 0, total: 0 });
    await getConsole("s1");
    expect(calls[0]?.url).toBe("/api/sessions/s1/console?since=0");
  });
  it("getConsole forwards since + level", async () => {
    const calls = installFetch({ messages: [], cursor: 0, total: 0 });
    await getConsole("s1", 7, "error");
    expect(calls[0]?.url).toBe("/api/sessions/s1/console?since=7&level=error");
  });
  it("getConsole skips level when 'all'", async () => {
    const calls = installFetch({ messages: [], cursor: 0, total: 0 });
    await getConsole("s1", 0, "all");
    expect(calls[0]?.url).toBe("/api/sessions/s1/console?since=0");
  });
  it("getDownloads forwards since", async () => {
    const calls = installFetch({ downloads: [], cursor: 0, total: 0 });
    await getDownloads("s1", 3);
    expect(calls[0]?.url).toBe("/api/sessions/s1/downloads?since=3");
  });
  it("openTrace POSTs", async () => {
    const calls = installFetch({ pid: 1, trace_path: "x" });
    await openTrace("s1");
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.url).toBe("/api/sessions/s1/trace/open");
  });
  it("getHealth", async () => {
    const calls = installFetch({ ok: true, version: "0.0.0" });
    await getHealth();
    expect(calls[0]?.url).toBe("/api/health");
  });
});

describe("url helpers", () => {
  it("videoUrl", () => {
    expect(videoUrl("a")).toBe("/api/sessions/a/video");
  });
  it("traceDownloadUrl", () => {
    expect(traceDownloadUrl("a")).toBe("/api/sessions/a/trace");
  });
  it("markdownUrl", () => {
    expect(markdownUrl("a")).toBe("/api/sessions/a/markdown");
  });
  it("frameUrl includes timestamp", () => {
    expect(frameUrl("a", 12.5)).toBe("/api/sessions/a/frame?t=12.5");
  });
  it("screenshotUrl encodes filename", () => {
    expect(screenshotUrl("a", "shot 1.png")).toBe("/api/sessions/a/screenshots/shot%201.png");
  });
  it("tailWebSocketUrl uses ws://", () => {
    const url = tailWebSocketUrl("a");
    expect(url.startsWith("ws://") || url.startsWith("wss://")).toBe(true);
    expect(url.endsWith("/api/sessions/a/tail")).toBe(true);
  });
  it("tailWebSocketUrl appends ?since when nonzero", () => {
    const url = tailWebSocketUrl("a", 42);
    expect(url.endsWith("/api/sessions/a/tail?since=42")).toBe(true);
  });
  it("tailWebSocketUrl omits ?since when zero", () => {
    const url = tailWebSocketUrl("a", 0);
    expect(url.includes("?")).toBe(false);
  });
  it("dashboardEventsUrl", () => {
    expect(dashboardEventsUrl()).toBe("/api/dashboard/events");
  });
});
