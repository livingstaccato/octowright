import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DashboardEventStreamOptions } from "./dashboard-events.js";
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

const dashboardEventMocks = vi.hoisted(() => ({
  openDashboardEventStream: vi.fn(),
}));

vi.mock("./dashboard-events.js", () => dashboardEventMocks);

const dashboard = await import("./dashboard.js");

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, ((event: MessageEvent) => void)[]> = {};
  onerror: ((event: Event) => void) | null;
  close = vi.fn();
  readonly url = "/api/dashboard/events";

  constructor(
    readonly options: DashboardEventStreamOptions,
    open = true,
  ) {
    FakeEventSource.instances.push(this);
    this.listeners.invalidate = [
      (event: MessageEvent) => {
        this.options.onInvalidate(event?.data);
      },
    ];
    this.onerror = (event) => this.options.onError?.(event);
    if (open) this.options.onOpen?.();
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
    dashboardEventMocks.openDashboardEventStream
      .mockReset()
      .mockImplementation((options: DashboardEventStreamOptions) => new FakeEventSource(options));
    root = document.createElement("div");
    document.body.append(root);
  });

  afterEach(() => {
    dashboard.disposeDashboard();
    root.remove();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("opens the authenticated fetch dashboard event stream", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);

    await dashboard.bootDashboard(root);

    expect(dashboardEventMocks.openDashboardEventStream).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0]?.url).toBe("/api/dashboard/events");
    expect(vi.getTimerCount()).toBe(0);
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

  it("preserves persona/macro DOM nodes across a sessions-scoped invalidation", async () => {
    // Regression test for the partial-update render: a sessions-only
    // invalidation must not rebuild the personas or macros panels, so
    // their DOM nodes (and any listeners attached to them) survive.
    apiMocks.getPersonas.mockResolvedValue([
      { name: "p1", display_name: "P1", engines: ["chromium"], path: "/p1", mtime: 0, last_used: "" },
    ]);
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);

    const personasPanelBefore = root.querySelector('[data-testid="panel-personas"]');
    const personasBodyBefore = personasPanelBefore?.children[1];
    const macrosPanelBefore = root.querySelector('[data-testid="panel-macros"]');
    const macrosBodyBefore = macrosPanelBefore?.children[1];

    // Attach a marker listener to the personas wrapper.
    let personasClicks = 0;
    personasPanelBefore?.addEventListener("click", () => {
      personasClicks += 1;
    });

    FakeEventSource.instances[0]?.listeners.invalidate?.[0]?.(
      new MessageEvent("invalidate", { data: '{"scope":"sessions"}' }),
    );
    await flushPromises();

    // Same wrapper, same body — no DOM rebuild.
    expect(root.querySelector('[data-testid="panel-personas"]')).toBe(personasPanelBefore);
    expect(personasPanelBefore?.children[1]).toBe(personasBodyBefore);
    expect(root.querySelector('[data-testid="panel-macros"]')).toBe(macrosPanelBefore);
    expect(macrosPanelBefore?.children[1]).toBe(macrosBodyBefore);

    // Listener still attached.
    personasPanelBefore?.dispatchEvent(new Event("click", { bubbles: true }));
    expect(personasClicks).toBe(1);
  });

  it("serializes overlapping scoped invalidations so updates aren't lost", async () => {
    // Regression test for an SSE race: two scoped invalidations firing close
    // together used to each snapshot the same currentState and merge against
    // it, with the second resolver overwriting the first's update.
    // Serializing tick() via a shared promise chain means the second tick
    // reads the first's committed state.
    apiMocks.getPersonas
      .mockResolvedValueOnce([]) // initial
      .mockResolvedValueOnce([
        { name: "p1", display_name: "P1", engines: ["chromium"], path: "/p1", mtime: 0, last_used: "" },
      ]); // personas tick

    const sessionsDeferred = deferred<SessionListResponse>();
    apiMocks.getSessions
      .mockResolvedValueOnce(emptySessions) // initial
      .mockReturnValueOnce(sessionsDeferred.promise); // sessions tick — gated

    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);

    // Fire sessions invalidation FIRST (its fetch is gated and won't resolve yet).
    FakeEventSource.instances[0]?.listeners.invalidate?.[0]?.(
      new MessageEvent("invalidate", { data: '{"scope":"sessions"}' }),
    );
    // Fire personas invalidation SECOND (its fetch resolves immediately on flush).
    FakeEventSource.instances[0]?.listeners.invalidate?.[0]?.(
      new MessageEvent("invalidate", { data: '{"scope":"personas"}' }),
    );

    // Let the personas tick try to run. With the race, it would resolve and
    // overwrite currentState before the sessions tick lands. With serialization,
    // it must wait for the sessions tick.
    await flushPromises();
    // Personas panel still shows the empty placeholder — personas tick is queued
    // behind the gated sessions tick.
    const personasBefore = root.querySelector('[data-testid="panel-personas"]')?.textContent ?? "";
    expect(personasBefore).not.toContain("P1");

    // Now release the sessions fetch.
    sessionsDeferred.resolve(sessionsWith("session-after-race"));
    await flushPromises();
    await flushPromises();
    await flushPromises();

    // Both updates should be visible: the sessions row AND the persona.
    const liveRows = root.querySelectorAll('[data-testid="panel-live-browsers"] tbody tr');
    expect(liveRows.length).toBe(1);
    expect(liveRows[0]?.textContent).toContain("session-after-race");
    expect(root.querySelector('[data-testid="panel-personas"]')?.textContent).toContain("P1");
  });

  it("keeps the panel registry in sync after a user-action refresh + later SSE invalidation", async () => {
    // Regression test for a real bug: the action handlers (delete /
    // relaunch / start-scenario) used to call renderDashboard directly,
    // which re-mounted the DOM but left bootDashboard's panel registry
    // pointing at the now-orphan nodes. The next SSE invalidation then
    // mutated detached nodes and the dashboard appeared frozen until a
    // full poll-fallback refresh.
    apiMocks.getSessions
      .mockResolvedValueOnce(emptySessions) // initial mount
      .mockResolvedValueOnce(sessionsWith("after-action")) // refreshDashboardNow
      .mockResolvedValueOnce(sessionsWith("after-sse")); // SSE invalidation
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);

    // Simulate the path an action handler now takes.
    await dashboard.refreshDashboardNow();
    await flushPromises();
    let liveRows = root.querySelectorAll('[data-testid="panel-live-browsers"] tbody tr');
    expect(liveRows.length).toBe(1);
    // The row should display the after-action id, proving refreshDashboardNow
    // updated the live DOM (not orphan nodes).
    expect(liveRows[0]?.textContent).toContain("after-action");

    // An SSE invalidation arrives after the action.
    FakeEventSource.instances[0]?.listeners.invalidate?.[0]?.(
      new MessageEvent("invalidate", { data: '{"scope":"sessions"}' }),
    );
    await flushPromises();

    // The DOM should reflect the SSE-fetched id. If the registry were
    // stale (the bug), the SSE update would mutate orphan nodes and the
    // visible row would still be "after-action".
    liveRows = root.querySelectorAll('[data-testid="panel-live-browsers"] tbody tr');
    expect(liveRows.length).toBe(1);
    expect(liveRows[0]?.textContent).toContain("after-sse");
  });

  it("keeps last-known panel data when a full action refresh partially fails", async () => {
    apiMocks.getSessions
      .mockResolvedValueOnce(sessionsWith("before-failure"))
      .mockRejectedValueOnce(new Error("sessions unavailable"));
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);

    await dashboard.refreshDashboardNow();
    await flushPromises();

    const liveRows = root.querySelectorAll('[data-testid="panel-live-browsers"] tbody tr');
    expect(liveRows).toHaveLength(1);
    expect(liveRows[0]?.textContent).toContain("before-failure");
    expect(root.querySelector('[data-testid="dashboard-degraded"]')?.textContent).toContain("Sessions");
  });

  it("rebuilds the live-browsers body on a sessions-scoped invalidation", async () => {
    // Counterpart to the test above: the matching scope's body IS replaced.
    apiMocks.getSessions.mockResolvedValueOnce(emptySessions).mockResolvedValueOnce(sessionsWith("s1"));
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);

    const liveBrowsersPanel = root.querySelector('[data-testid="panel-live-browsers"]');
    const liveBrowsersBodyBefore = liveBrowsersPanel?.children[1];

    FakeEventSource.instances[0]?.listeners.invalidate?.[0]?.(
      new MessageEvent("invalidate", { data: '{"scope":"sessions"}' }),
    );
    await flushPromises();

    // Same wrapper, but body replaced.
    expect(root.querySelector('[data-testid="panel-live-browsers"]')).toBe(liveBrowsersPanel);
    expect(liveBrowsersPanel?.children[1]).not.toBe(liveBrowsersBodyBefore);
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

  it("keeps polling until the fetch event stream opens", async () => {
    dashboardEventMocks.openDashboardEventStream.mockImplementationOnce(
      (options: DashboardEventStreamOptions) => new FakeEventSource(options, false),
    );
    await dashboard.bootDashboard(root);
    expect(apiMocks.getSessions).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5000);

    expect(apiMocks.getSessions).toHaveBeenCalledTimes(2);
  });

  it("enters a terminal re-pair state instead of opening retry transports after 401", async () => {
    apiMocks.getSessions.mockImplementationOnce(async () => {
      window.dispatchEvent(new Event("octowright:dashboard-auth-required"));
      throw new Error("pairing required");
    });

    await dashboard.bootDashboard(root);

    expect(dashboardEventMocks.openDashboardEventStream).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("Dashboard pairing expired");
    await vi.advanceTimersByTimeAsync(15_000);
    expect(apiMocks.getSessions).toHaveBeenCalledOnce();
  });

  it("keeps the reconnecting fetch stream open and polls after an error", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    await dashboard.bootDashboard(root);
    const source = FakeEventSource.instances[0];

    source?.onerror?.(new Event("error"));
    await vi.advanceTimersByTimeAsync(5000);

    expect(source?.close).toHaveBeenCalledTimes(0);
    expect(apiMocks.getSessions).toHaveBeenCalledTimes(2);
  });

  it("returns a disposer that closes the stream and clears polling", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);

    const dispose = await dashboard.bootDashboard(root);
    expect(typeof dispose).toBe("function");
    expect(vi.getTimerCount()).toBe(0);

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
    expect(vi.getTimerCount()).toBe(0);
  });

  it("the latest invalidation wins regardless of fetch resolution timing", async () => {
    // With serialized ticks, two invalidations fire requests sequentially —
    // the second only starts after the first commits. Both are visible in
    // their committed order; the visible end state reflects the last tick.
    vi.stubGlobal("EventSource", FakeEventSource);
    const first = deferred<SessionListResponse>();
    const second = deferred<SessionListResponse>();
    apiMocks.getSessions
      .mockResolvedValueOnce(emptySessions)
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);

    await dashboard.bootDashboard(root);
    const source = FakeEventSource.instances[0];

    source?.listeners.invalidate?.[0]?.(new MessageEvent("invalidate"));
    source?.listeners.invalidate?.[0]?.(new MessageEvent("invalidate"));

    // Resolve the second fetch BEFORE the first — it shouldn't matter.
    second.resolve(sessionsWith("second-wins"));
    first.resolve(sessionsWith("first"));
    await flushPromises();
    await flushPromises();
    await flushPromises();

    // End state is the second tick's payload (the latest invalidation).
    const liveRows = root.querySelectorAll('[data-testid="panel-live-browsers"] tbody tr');
    expect(liveRows.length).toBe(1);
    expect(liveRows[0]?.textContent).toContain("second-wins");
  });
});
