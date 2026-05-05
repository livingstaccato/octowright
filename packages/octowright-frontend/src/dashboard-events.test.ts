import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  await Promise.resolve();
  await Promise.resolve();
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
});
