import { beforeEach, describe, expect, it, vi } from "vitest";

import { bootStreamSession, importRenderer } from "./session-stream.js";
import type { StreamHandle } from "./plugin-contract.js";

vi.mock("./api.js", () => ({
  getEvents: vi.fn().mockResolvedValue({
    events: [{ ts: "2026-08-24T00:00:00Z", action: "ref_ready" }],
    cursor: 42,
  }),
  tailWebSocketUrl: vi.fn().mockReturnValue("ws://x/tail"),
}));

const detail = { kind: "refkind", live: false, id: "s1" } as never;
const liveDetail = { kind: "refkind", live: true, id: "s1" } as never;

interface Listener {
  type: string;
  handler: (e: unknown) => void;
}

// Mirrors session-terminal.test.ts's FakeWebSocket: a minimal double so
// openTail's addEventListener/emit contract can be exercised without a real
// socket.
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  listeners: Listener[] = [];
  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }
  addEventListener(type: string, handler: (e: unknown) => void): void {
    this.listeners.push({ type, handler });
  }
  emit(type: string, event: unknown): void {
    for (const l of this.listeners) if (l.type === type) l.handler(event);
  }
  close(): void {}
}

function recordingMount() {
  const fed: unknown[][] = [];
  let destroyed = 0;
  const mount = vi.fn(
    (): StreamHandle => ({
      feed: (events) => fed.push(events),
      destroy: () => {
        destroyed += 1;
      },
    }),
  );
  return { mount, fed, destroyed: () => destroyed };
}

describe("bootStreamSession", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
  });

  it("gives the plugin a mount element and feeds it recorded history", async () => {
    const root = document.createElement("div");
    const { mount, fed } = recordingMount();
    await bootStreamSession(root, "s1", detail, mount);
    expect(mount).toHaveBeenCalledOnce();
    expect(fed[0]).toEqual([{ ts: "2026-08-24T00:00:00Z", action: "ref_ready" }]);
  });

  it("renders core's chrome, not the plugin's", async () => {
    const root = document.createElement("div");
    const { mount } = recordingMount();
    await bootStreamSession(root, "s1", detail, mount);
    expect(root.querySelector('[data-testid="session-header"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="session-timeline"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="session-footer"]')).not.toBeNull();
  });

  it("awaits an async mountStream before the first feed", async () => {
    const root = document.createElement("div");
    const order: string[] = [];
    const mount = vi.fn(async () => {
      await Promise.resolve();
      order.push("mounted");
      return { feed: () => order.push("fed"), destroy: () => {} };
    });
    await bootStreamSession(root, "s1", detail, mount);
    expect(order).toEqual(["mounted", "fed"]);
  });

  it("falls back with a reason when mountStream throws", async () => {
    const root = document.createElement("div");
    const mount = vi.fn(() => {
      throw new TypeError("x is not a function");
    });
    await bootStreamSession(root, "s1", detail, mount);
    const notice = root.querySelector('[data-testid="stream-fallback-notice"]');
    expect(notice).not.toBeNull();
    expect(notice?.getAttribute("data-fallback-code")).toBe("mount-failed");
    expect(notice?.textContent).toContain("x is not a function");
  });

  it("falls back with a stringified reason when mountStream throws a non-Error value", async () => {
    const root = document.createElement("div");
    const mount = vi.fn(() => {
      // A raw string, not an Error -- exercises errorMessage()'s non-Error branch.
      throw "raw string thrown by a careless plugin";
    });
    await bootStreamSession(root, "s1", detail, mount);
    const notice = root.querySelector('[data-testid="stream-fallback-notice"]');
    expect(notice?.textContent).toContain("raw string thrown by a careless plugin");
  });

  it("falls back when an async mountStream rejects, and destroys nothing", async () => {
    const root = document.createElement("div");
    const mount = vi.fn(async () => {
      throw new Error("async boom");
    });
    await bootStreamSession(root, "s1", detail, mount);
    expect(root.querySelector('[data-testid="stream-fallback-notice"]')).not.toBeNull();
  });

  it("a feed that throws switches to the fallback rather than breaking the page, destroying the plugin's original handle exactly once", async () => {
    const root = document.createElement("div");
    // A destroy SPY, not a plain stub: the point of this test is that the
    // discarded plugin handle (which may own a socket/timer/observer) is
    // released before it is replaced by the fallback, not merely that the
    // page keeps working.
    const destroySpy = vi.fn();
    const mount = vi.fn(
      (): StreamHandle => ({
        feed: () => {
          throw new Error("feed exploded");
        },
        destroy: destroySpy,
      }),
    );
    await bootStreamSession(root, "s1", detail, mount);
    const notice = root.querySelector('[data-testid="stream-fallback-notice"]');
    expect(notice?.getAttribute("data-fallback-code")).toBe("mount-failed");
    expect(destroySpy).toHaveBeenCalledOnce();
  });

  it("swaps to the fallback even when the discarded handle's own destroy throws", async () => {
    const root = document.createElement("div");
    const mount = vi.fn(
      (): StreamHandle => ({
        feed: () => {
          throw new Error("feed exploded");
        },
        destroy: () => {
          throw new Error("destroy exploded too");
        },
      }),
    );
    // Must not reject: failure-handling code cannot itself have a failure mode.
    await expect(bootStreamSession(root, "s1", detail, mount)).resolves.toBeUndefined();
    const notice = root.querySelector('[data-testid="stream-fallback-notice"]');
    expect(notice?.getAttribute("data-fallback-code")).toBe("mount-failed");
  });

  it("opens a live tail via the injected WebSocket ctor and feeds live deltas after history", async () => {
    const root = document.createElement("div");
    const { mount, fed } = recordingMount();

    await bootStreamSession(root, "s1", liveDetail, mount, {
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });

    // History was fed synchronously during boot -- before bootStreamSession
    // resolved, and therefore strictly before any live frame could exist.
    expect(fed).toEqual([[{ ts: "2026-08-24T00:00:00Z", action: "ref_ready" }]]);
    expect(FakeWebSocket.instances).toHaveLength(1);

    FakeWebSocket.instances[0]?.emit("message", {
      data: JSON.stringify({
        events: [{ ts: "2026-08-24T00:00:05Z", action: "ref_delta" }],
        cursor: 100,
      }),
    });

    // The order pins the "history before live" guarantee the same way
    // "awaits an async mountStream before the first feed" pins mount-before-
    // feed above: by asserting the SEQUENCE `feed` was called in, not merely
    // that it was called.
    expect(fed).toEqual([
      [{ ts: "2026-08-24T00:00:00Z", action: "ref_ready" }],
      [{ ts: "2026-08-24T00:00:05Z", action: "ref_delta" }],
    ]);
  });

  it("does not open a tail for a closed (non-live) session", async () => {
    const root = document.createElement("div");
    const { mount } = recordingMount();
    await bootStreamSession(root, "s1", detail, mount, {
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("derives the timeline base timestamp from the first live event when history is empty", async () => {
    const { getEvents } = await import("./api.js");
    vi.mocked(getEvents).mockResolvedValueOnce({ events: [], cursor: 0 } as never);

    const root = document.createElement("div");
    const { mount, fed } = recordingMount();
    await bootStreamSession(root, "s1", liveDetail, mount, {
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });

    expect(fed).toEqual([[]]); // empty history still feeds an (empty) batch

    FakeWebSocket.instances[0]?.emit("message", {
      data: JSON.stringify({
        events: [{ ts: "2026-08-24T00:00:09Z", action: "ref_delta" }],
        cursor: 1,
      }),
    });

    expect(fed).toEqual([[], [{ ts: "2026-08-24T00:00:09Z", action: "ref_delta" }]]);
  });
});

describe("importRenderer", () => {
  it("turns a nonexistent relative module into an import-failed reason", async () => {
    const result = await importRenderer("./__does_not_exist_session_stream_fixture__.js");
    expect(result).toMatchObject({ code: "import-failed" });
    expect((result as { detail: string }).detail.length).toBeGreaterThan(0);
  });

  it("turns an unreachable absolute URL into an import-failed reason", async () => {
    const result = await importRenderer("http://127.0.0.1:1/renderer.js");
    expect(result).toMatchObject({ code: "import-failed" });
  });

  it("resolves a genuine successful import to the non-error shape", async () => {
    // A real, side-effect-free sibling module. importRenderer does not
    // validate that it exports `mountStream` -- that surfaces later, at the
    // mount call site -- so this only proves the try/catch around import()
    // does not misclassify a SUCCESSFUL import as a failure.
    const result = await importRenderer("./session-fallback.js");
    expect("code" in result).toBe(false);
  });
});
