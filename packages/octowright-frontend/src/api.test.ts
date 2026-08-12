import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  dashboardEventsUrl,
  deleteRecording,
  fetchJson,
  frameUrl,
  getConsole,
  getDownloads,
  getEvents,
  getHealth,
  getMacro,
  getMacroRepairPreview,
  getMacros,
  getPersonaDetail,
  getPersonaSizes,
  getPersonas,
  getScenarios,
  getScreenshots,
  getSession,
  getSessions,
  liveScreenshotUrl,
  markdownUrl,
  openTrace,
  pathTemplate,
  relaunchSession,
  screenshotUrl,
  startScenario,
  tailWebSocketUrl,
  traceDownloadUrl,
  updateMacro,
  updatePersonaYaml,
  validateMacro,
  validateSessionSelector,
  videoUrl,
} from "./api.js";
import { getDashboardBearer, setDashboardBearer } from "./dashboard-auth.js";

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
  sessionStorage.clear();
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
  it("uses server message details when error is absent", async () => {
    installFetch({ message: "try again later" }, 503);
    await expect(fetchJson("/api/x")).rejects.toThrow("try again later");
  });
  it("skips blank server error fields before using message details", async () => {
    installFetch({ error: "  ", message: "fallback message" }, 400);
    await expect(fetchJson("/api/x")).rejects.toThrow("fallback message");
  });
  it("falls back to status text when error body is not JSON", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: async () => {
        throw new Error("not json");
      },
    })) as unknown as typeof fetch;

    await expect(fetchJson("/api/x")).rejects.toThrow("request failed: 502 Bad Gateway");
  });
  it("records and rethrows fetch exceptions", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError("network down");
    }) as unknown as typeof fetch;

    await expect(fetchJson("/api/x")).rejects.toThrow("network down");
  });
  it("falls back to Date.now when performance.now is unavailable", async () => {
    vi.stubGlobal("performance", undefined);
    const calls = installFetch({ ok: true });

    await fetchJson("/api/no-performance");

    expect(calls[0]?.url).toBe("/api/no-performance");
  });
  it("forwards AbortSignal", async () => {
    const calls = installFetch({});
    const ac = new AbortController();
    await fetchJson("/api/x", { signal: ac.signal });
    expect(calls[0]?.init?.signal).toBe(ac.signal);
  });
  it("attaches a live bearer while preserving caller headers", async () => {
    setDashboardBearer({ bearer: "dash-secret", expires_at: Date.now() / 1000 + 60 });
    const calls = installFetch({ ok: true });
    await fetchJson("/api/x", { headers: { "X-Custom": "present" } });
    const headers = new Headers(calls[0]?.init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer dash-secret");
    expect(headers.get("X-Custom")).toBe("present");
  });
  it("leaves unpaired requests without Authorization", async () => {
    const calls = installFetch({ ok: true });
    await fetchJson("/api/x");
    expect(new Headers(calls[0]?.init?.headers).has("Authorization")).toBe(false);
  });
  it("clears a bearer after an authenticated 401", async () => {
    setDashboardBearer({ bearer: "dash-secret", expires_at: Date.now() / 1000 + 60 });
    installFetch({ error: "pairing required" }, 401);
    await expect(fetchJson("/api/x")).rejects.toBeInstanceOf(ApiError);
    expect(getDashboardBearer()).toBeNull();
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
    const calls = installFetch({ ok: true, valid: true, issues: [], issue_count: 0, error_count: 0 });
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
    const calls = installFetch({ ok: true, found: true, count: 1, selector: "#x" });
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
  it("getPersonaDetail encodes persona names", async () => {
    const calls = installFetch({ name: "qa/user" });
    await getPersonaDetail("qa/user");
    expect(calls[0]?.url).toBe("/api/personas/qa%2Fuser");
  });
  it("updatePersonaYaml puts YAML body", async () => {
    const calls = installFetch({ ok: true, name: "qa" });
    await updatePersonaYaml("qa", "name: qa\n");
    expect(calls[0]?.url).toBe("/api/personas/qa");
    expect(calls[0]?.init?.method).toBe("PUT");
    expect(calls[0]?.init?.body).toBe(JSON.stringify({ yaml: "name: qa\n" }));
  });
  it("deleteRecording deletes the encoded recording endpoint", async () => {
    const calls = installFetch({ deleted: true, session_id: "s/1", files_removed: 1 });
    await deleteRecording("s/1");
    expect(calls[0]?.url).toBe("/api/sessions/s%2F1/recording");
    expect(calls[0]?.init?.method).toBe("DELETE");
  });
  it("relaunchSession posts to the relaunch endpoint", async () => {
    const calls = installFetch({ id: "new", kind: "chromium" });
    await relaunchSession("old/1");
    expect(calls[0]?.url).toBe("/api/sessions/old%2F1/relaunch");
    expect(calls[0]?.init?.method).toBe("POST");
  });
  it("startScenario posts to the encoded scenario endpoint", async () => {
    const calls = installFetch({ scenario_id: "s1", name: "two/user", participants: [] });
    await startScenario("two/user");
    expect(calls[0]?.url).toBe("/api/scenarios/two%2Fuser/start");
    expect(calls[0]?.init?.method).toBe("POST");
  });
  it("getPersonaSizes hits the sizes endpoint", async () => {
    const calls = installFetch({ qa: 1024 });
    await getPersonaSizes();
    expect(calls[0]?.url).toBe("/api/personas/sizes");
  });
});

describe("url helpers", () => {
  it("pathTemplate strips queries and templates dynamic ids", () => {
    expect(pathTemplate("/api/sessions/abc/events?since=1")).toBe("/api/sessions/{id}/events");
    expect(pathTemplate("/api/sessions/abc/screenshots/shot 1.png")).toBe("/api/sessions/{id}/screenshots/{file}");
    expect(pathTemplate("/api/sessions/abc/screenshot/now")).toBe("/api/sessions/{id}/screenshot/now");
    expect(pathTemplate("/api/sessions/abc/frame")).toBe("/api/sessions/{id}/frame");
  });
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
  it("tailWebSocketUrl falls back without window", () => {
    vi.stubGlobal("window", undefined);
    expect(tailWebSocketUrl("a")).toBe("ws://localhost/api/sessions/a/tail");
  });
  it("dashboardEventsUrl", () => {
    expect(dashboardEventsUrl()).toBe("/api/dashboard/events");
  });
  it("liveScreenshotUrl includes optional jpeg, quality, full-page, and cache-bust params", () => {
    expect(liveScreenshotUrl("a/b")).toBe("/api/sessions/a%2Fb/screenshot/now?format=png");
    expect(liveScreenshotUrl("a", { format: "jpeg", quality: 75, fullPage: true, cacheBust: 123 })).toBe(
      "/api/sessions/a/screenshot/now?format=jpeg&quality=75&full_page=true&_=123",
    );
    expect(liveScreenshotUrl("a", { quality: 75 })).toBe("/api/sessions/a/screenshot/now?format=png");
  });
});
