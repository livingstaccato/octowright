import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  fetchJson,
  frameUrl,
  getEvents,
  getHealth,
  getMacros,
  getPersonas,
  getScenarios,
  getScreenshots,
  getSession,
  getSessions,
  openTrace,
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
  it("getScreenshots", async () => {
    const calls = installFetch({ screenshots: [] });
    await getScreenshots("s1");
    expect(calls[0]?.url).toBe("/api/sessions/s1/screenshots");
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
});
