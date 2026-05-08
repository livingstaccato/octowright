import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionListResponse, SessionSummary } from "./types.js";

const emptySessions = { live: [], closed: [] };
const emptyScenarios = { live: [] };

const apiMocks = vi.hoisted(() => ({
  dashboardEventsUrl: vi.fn(() => "/api/dashboard/events"),
  deleteRecording: vi.fn(),
  getMacros: vi.fn(async () => []),
  getPersonaDetail: vi.fn(),
  getPersonas: vi.fn(async () => []),
  getPersonaSizes: vi.fn(async () => ({})),
  getScenarios: vi.fn(async () => ({ live: [] })),
  getSessions: vi.fn(async () => ({ live: [], closed: [] })),
  relaunchSession: vi.fn(),
  startScenario: vi.fn(),
  updatePersonaYaml: vi.fn(),
}));

vi.mock("./api.js", () => apiMocks);

const dashboard = await import("./dashboard.js");

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, ((event: MessageEvent) => void)[]> = {};
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners[type] = [...(this.listeners[type] ?? []), listener];
  }
}

function resetApiMocks(): void {
  apiMocks.dashboardEventsUrl.mockClear();
  apiMocks.getMacros.mockClear().mockResolvedValue([]);
  apiMocks.getPersonaSizes.mockClear().mockResolvedValue({});
  apiMocks.getPersonas.mockClear().mockResolvedValue([]);
  apiMocks.getScenarios.mockClear().mockResolvedValue(emptyScenarios);
  apiMocks.getSessions.mockClear().mockResolvedValue(emptySessions);
}

async function flushPromises(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await Promise.resolve();
  }
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function sessionsWith(id: string): SessionListResponse {
  const row: SessionSummary = {
    id,
    kind: "chromium",
    label: id,
    profile: null,
    url: "https://example.test",
    started_at: "2026-05-05T00:00:00Z",
    live: true,
    log_path: `${id}.jsonl`,
  };
  return { live: [row], closed: [] };
}

describe("bootDashboard dashboard invalidation stream", () => {
  let root: HTMLDivElement;

  beforeEach(() => {
    vi.useFakeTimers();
    resetApiMocks();
    FakeEventSource.instances = [];
    root = document.createElement("div");
    document.body.append(root);
  });

  afterEach(() => {
    dashboard.disposeDashboard();
    root.remove();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("opens the dashboard event stream when EventSource exists", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);

    await dashboard.bootDashboard(root);

    expect(apiMocks.dashboardEventsUrl).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0]?.url).toBe("/api/dashboard/events");
  });

  it("refreshes immediately on invalidation messages", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);
    expect(apiMocks.getSessions).toHaveBeenCalledTimes(1);

    FakeEventSource.instances[0]?.listeners.invalidate?.[0]?.(
      new MessageEvent("invalidate", { data: '{"scope":"sessions"}' }),
    );
    await flushPromises();

    expect(apiMocks.getSessions).toHaveBeenCalledTimes(2);
  });

  it("refreshes only the requested scope for sessions invalidation", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);
    expect(apiMocks.getSessions).toHaveBeenCalledTimes(1);
    expect(apiMocks.getScenarios).toHaveBeenCalledTimes(1);
    expect(apiMocks.getPersonas).toHaveBeenCalledTimes(1);
    expect(apiMocks.getMacros).toHaveBeenCalledTimes(1);

    FakeEventSource.instances[0]?.listeners.invalidate?.[0]?.(
      new MessageEvent("invalidate", { data: '{"scope":"sessions"}' }),
    );
    await flushPromises();

    expect(apiMocks.getSessions).toHaveBeenCalledTimes(2);
    expect(apiMocks.getScenarios).toHaveBeenCalledTimes(1);
    expect(apiMocks.getPersonas).toHaveBeenCalledTimes(1);
    expect(apiMocks.getMacros).toHaveBeenCalledTimes(1);
  });

  it("refreshes all slices when invalidation payload omits scope", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);
    expect(apiMocks.getSessions).toHaveBeenCalledTimes(1);
    expect(apiMocks.getScenarios).toHaveBeenCalledTimes(1);
    expect(apiMocks.getPersonas).toHaveBeenCalledTimes(1);
    expect(apiMocks.getMacros).toHaveBeenCalledTimes(1);

    FakeEventSource.instances[0]?.listeners.invalidate?.[0]?.(new MessageEvent("invalidate"));
    await flushPromises();

    expect(apiMocks.getSessions).toHaveBeenCalledTimes(2);
    expect(apiMocks.getScenarios).toHaveBeenCalledTimes(2);
    expect(apiMocks.getPersonas).toHaveBeenCalledTimes(2);
    expect(apiMocks.getMacros).toHaveBeenCalledTimes(2);
  });

  it("refreshes all slices when invalidation payload has unknown scope", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);
    expect(apiMocks.getSessions).toHaveBeenCalledTimes(1);
    expect(apiMocks.getScenarios).toHaveBeenCalledTimes(1);
    expect(apiMocks.getPersonas).toHaveBeenCalledTimes(1);
    expect(apiMocks.getMacros).toHaveBeenCalledTimes(1);

    FakeEventSource.instances[0]?.listeners.invalidate?.[0]?.(
      new MessageEvent("invalidate", { data: '{"scope":"not-a-real-scope"}' }),
    );
    await flushPromises();

    expect(apiMocks.getSessions).toHaveBeenCalledTimes(2);
    expect(apiMocks.getScenarios).toHaveBeenCalledTimes(2);
    expect(apiMocks.getPersonas).toHaveBeenCalledTimes(2);
    expect(apiMocks.getMacros).toHaveBeenCalledTimes(2);
  });

  it("supports comma-separated scoped invalidations", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);
    expect(apiMocks.getSessions).toHaveBeenCalledTimes(1);
    expect(apiMocks.getScenarios).toHaveBeenCalledTimes(1);
    expect(apiMocks.getPersonas).toHaveBeenCalledTimes(1);
    expect(apiMocks.getMacros).toHaveBeenCalledTimes(1);

    FakeEventSource.instances[0]?.listeners.invalidate?.[0]?.(
      new MessageEvent("invalidate", { data: '{"scope":"sessions,macros"}' }),
    );
    await flushPromises();

    expect(apiMocks.getSessions).toHaveBeenCalledTimes(2);
    expect(apiMocks.getScenarios).toHaveBeenCalledTimes(1);
    expect(apiMocks.getPersonas).toHaveBeenCalledTimes(1);
    expect(apiMocks.getMacros).toHaveBeenCalledTimes(2);
  });

  it("keeps polling when EventSource is missing", async () => {
    vi.stubGlobal("EventSource", undefined);
    await dashboard.bootDashboard(root);
    expect(apiMocks.getSessions).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5000);

    expect(apiMocks.getSessions).toHaveBeenCalledTimes(2);
  });

  it("closes a failed EventSource and continues polling", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);
    const source = FakeEventSource.instances[0];

    source?.onerror?.(new Event("error"));
    await vi.advanceTimersByTimeAsync(5000);

    expect(source?.close).toHaveBeenCalledTimes(1);
    expect(apiMocks.getSessions).toHaveBeenCalledTimes(2);
  });

  it("returns a disposer that closes the stream and clears polling", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);

    const dispose = await dashboard.bootDashboard(root);
    expect(typeof dispose).toBe("function");
    expect(vi.getTimerCount()).toBe(1);

    dispose();

    expect(FakeEventSource.instances[0]?.close).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("rebooting closes the previous stream and replaces the poll interval", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);

    await dashboard.bootDashboard(root);
    const first = FakeEventSource.instances[0];
    await dashboard.bootDashboard(root);

    expect(first?.close).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(vi.getTimerCount()).toBe(1);
  });

  it("suppresses stale refresh renders when an older request resolves last", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const slow = deferred<SessionListResponse>();
    const fast = deferred<SessionListResponse>();
    apiMocks.getSessions
      .mockResolvedValueOnce(emptySessions)
      .mockImplementationOnce(() => slow.promise)
      .mockImplementationOnce(() => fast.promise);

    await dashboard.bootDashboard(root);
    const source = FakeEventSource.instances[0];

    source?.listeners.invalidate?.[0]?.(new MessageEvent("invalidate"));
    source?.listeners.invalidate?.[0]?.(new MessageEvent("invalidate"));
    fast.resolve(sessionsWith("newer"));
    await flushPromises();
    expect(root.textContent).toContain("newer");

    slow.resolve(sessionsWith("older"));
    await flushPromises();
    expect(root.textContent).toContain("newer");
    expect(root.textContent).not.toContain("older");
  });
});
