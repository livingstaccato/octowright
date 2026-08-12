import { afterEach, describe, expect, it, vi } from "vitest";
import { screencastWsUrl } from "./api.js";
import { DASHBOARD_AUTH_REQUIRED_EVENT, getDashboardBearer, setDashboardBearer } from "./dashboard-auth.js";
import { openScreencast } from "./live-preview-screencast.js";

class FakeWS {
  listeners: Record<string, EventListener[]> = {};
  binaryType: BinaryType = "arraybuffer";
  close = vi.fn();

  constructor(
    readonly url = "",
    readonly protocols?: string | string[],
  ) {}

  addEventListener(type: string, cb: EventListener) {
    const listeners = this.listeners[type] ?? [];
    listeners.push(cb);
    this.listeners[type] = listeners;
  }
  emit(type: string, e: Event) {
    for (const cb of this.listeners[type] ?? []) {
      cb(e);
    }
  }
}

function webSocketCtorRecording(instances: FakeWS[]): typeof WebSocket {
  return class extends FakeWS {
    constructor(url: string, protocols?: string | string[]) {
      super(url, protocols);
      instances.push(this);
    }
  } as unknown as typeof WebSocket;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("openScreencast", () => {
  it("uses the global WebSocket constructor when one is not injected", () => {
    const instances: FakeWS[] = [];
    vi.stubGlobal(
      "WebSocket",
      class extends FakeWS {
        constructor(url: string) {
          super(url);
          instances.push(this);
        }
      },
    );
    openScreencast("ws://x/screencast", { onFrame: () => {} });
    expect(instances).toHaveLength(1);
  });

  it("sets binaryType to blob", () => {
    const instances: FakeWS[] = [];
    openScreencast("ws://x/screencast", {
      onFrame: () => {},
      webSocketCtor: webSocketCtorRecording(instances),
    });
    expect(instances[0]?.binaryType).toBe("blob");
  });

  it("passes stable and credential protocols without putting the bearer in the URL", () => {
    sessionStorage.clear();
    setDashboardBearer({ bearer: "cast-secret", expires_at: Date.now() / 1000 + 60 });
    const instances: FakeWS[] = [];
    openScreencast("ws://x/screencast", {
      onFrame: () => {},
      webSocketCtor: webSocketCtorRecording(instances),
    });
    expect(instances[0]?.url).not.toContain("cast-secret");
    expect(instances[0]?.protocols).toEqual(["octowright.dashboard", "octowright.dashboard.bearer.cast-secret"]);
    sessionStorage.clear();
  });

  it("delivers binary frames as Blobs", () => {
    const instances: FakeWS[] = [];
    const frames: Blob[] = [];
    openScreencast("ws://x/screencast", {
      onFrame: (b) => frames.push(b),
      webSocketCtor: webSocketCtorRecording(instances),
    });
    const ws = instances[0];
    if (!ws) throw new Error("socket missing");
    ws.emit("message", new MessageEvent("message", { data: new Blob([new Uint8Array([1, 2, 3])]) }));
    expect(frames.length).toBe(1);
  });

  it("ignores non-Blob messages", () => {
    const instances: FakeWS[] = [];
    const onFrame = vi.fn();
    openScreencast("ws://x/screencast", {
      onFrame,
      webSocketCtor: webSocketCtorRecording(instances),
    });
    const ws = instances[0];
    if (!ws) throw new Error("socket missing");
    ws.emit("message", new MessageEvent("message", { data: new Uint8Array([1, 2, 3]) }));
    expect(onFrame).not.toHaveBeenCalled();
  });

  it("close() closes the socket", () => {
    const instances: FakeWS[] = [];
    const handle = openScreencast("ws://x", {
      onFrame: () => {},
      webSocketCtor: webSocketCtorRecording(instances),
    });
    const ws = instances[0];
    if (!ws) throw new Error("socket missing");
    handle.close();
    expect(ws.close).toHaveBeenCalled();
  });

  it("close() does not throw if socket close throws", () => {
    const instances: FakeWS[] = [];
    const handle = openScreencast("ws://x", {
      onFrame: () => {},
      webSocketCtor: webSocketCtorRecording(instances),
    });
    const ws = instances[0];
    if (!ws) throw new Error("socket missing");
    ws.close.mockImplementation(() => {
      throw new Error("close failed");
    });
    expect(() => handle.close()).not.toThrow();
  });

  it("forwards error and close callbacks", () => {
    const instances: FakeWS[] = [];
    const onError = vi.fn();
    const onClose = vi.fn();
    openScreencast("ws://x", {
      onFrame: () => {},
      onError,
      onClose,
      webSocketCtor: webSocketCtorRecording(instances),
    });
    const ws = instances[0];
    if (!ws) throw new Error("socket missing");
    ws.emit("error", new Event("error"));
    const closeEvent = new Event("close") as CloseEvent;
    Object.defineProperties(closeEvent, {
      code: { value: 1000 },
      reason: { value: "ok" },
      wasClean: { value: true },
    });
    ws.emit("close", closeEvent);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clears auth and enters the re-pair state on an established-stream auth close", () => {
    sessionStorage.clear();
    setDashboardBearer({ bearer: "expired-cast-secret", expires_at: Date.now() / 1000 + 60 });
    const authRequired = vi.fn();
    window.addEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, authRequired);
    const instances: FakeWS[] = [];
    openScreencast("ws://x", {
      onFrame: () => {},
      webSocketCtor: webSocketCtorRecording(instances),
    });
    const closeEvent = new Event("close") as CloseEvent;
    Object.defineProperties(closeEvent, {
      code: { value: 1008 },
      reason: { value: "dashboard pairing expired" },
      wasClean: { value: true },
    });

    instances[0]?.emit("close", closeEvent);

    expect(getDashboardBearer()).toBeNull();
    expect(authRequired).toHaveBeenCalledOnce();
    window.removeEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, authRequired);
  });

  it("enters the re-pair state when initial admission closes with an observable auth reason", () => {
    sessionStorage.clear();
    setDashboardBearer({ bearer: "rejected-cast-secret", expires_at: Date.now() / 1000 + 60 });
    const authRequired = vi.fn();
    window.addEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, authRequired);
    const instances: FakeWS[] = [];
    openScreencast("ws://x", {
      onFrame: () => {},
      webSocketCtor: webSocketCtorRecording(instances),
    });
    const closeEvent = new Event("close") as CloseEvent;
    Object.defineProperties(closeEvent, {
      code: { value: 1008 },
      reason: { value: "dashboard pairing required" },
      wasClean: { value: true },
    });

    instances[0]?.emit("close", closeEvent);

    expect(getDashboardBearer()).toBeNull();
    expect(authRequired).toHaveBeenCalledOnce();
    window.removeEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, authRequired);
  });
});

describe("screencastWsUrl", () => {
  it("uses ws protocol, host, encoded id, and optional fps", () => {
    const url = screencastWsUrl("a/b", { fps: 12 });
    expect(url.startsWith("ws://") || url.startsWith("wss://")).toBe(true);
    expect(url.endsWith("/api/sessions/a%2Fb/screencast?fps=12")).toBe(true);
  });

  it("omits fps when absent", () => {
    const url = screencastWsUrl("a");
    expect(url.endsWith("/api/sessions/a/screencast")).toBe(true);
    expect(url.includes("?")).toBe(false);
  });

  it("falls back without window", () => {
    vi.stubGlobal("window", undefined);
    expect(screencastWsUrl("a")).toBe("ws://localhost/api/sessions/a/screencast");
  });
});
