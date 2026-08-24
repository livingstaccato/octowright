import { describe, expect, it, vi } from "vitest";

import { bootStreamSession } from "./session-stream.js";
import type { StreamHandle } from "./plugin-contract.js";

vi.mock("./api.js", () => ({
  getEvents: vi.fn().mockResolvedValue({
    events: [{ ts: "2026-08-24T00:00:00Z", action: "ref_ready" }],
    cursor: 42,
  }),
  tailWebSocketUrl: vi.fn().mockReturnValue("ws://x/tail"),
}));

const detail = { kind: "refkind", live: false, id: "s1" } as never;

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

  it("falls back when an async mountStream rejects, and destroys nothing", async () => {
    const root = document.createElement("div");
    const mount = vi.fn(async () => {
      throw new Error("async boom");
    });
    await bootStreamSession(root, "s1", detail, mount);
    expect(root.querySelector('[data-testid="stream-fallback-notice"]')).not.toBeNull();
  });

  it("a feed that throws switches to the fallback rather than breaking the page", async () => {
    const root = document.createElement("div");
    const mount = vi.fn(
      (): StreamHandle => ({
        feed: () => {
          throw new Error("feed exploded");
        },
        destroy: () => {},
      }),
    );
    await bootStreamSession(root, "s1", detail, mount);
    const notice = root.querySelector('[data-testid="stream-fallback-notice"]');
    expect(notice?.getAttribute("data-fallback-code")).toBe("mount-failed");
  });
});
