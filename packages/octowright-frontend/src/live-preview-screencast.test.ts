import { afterEach, describe, expect, it, vi } from "vitest";
import { screencastWsUrl } from "./api.js";
import { openScreencast } from "./live-preview-screencast.js";

class FakeWS {
  listeners: Record<string, ((e: any) => void)[]> = {};
  binaryType: BinaryType = "arraybuffer";
  close = vi.fn();
  addEventListener(type: string, cb: (e: any) => void) {
    (this.listeners[type] ??= []).push(cb);
  }
  emit(type: string, e: any) {
    (this.listeners[type] ?? []).forEach((cb) => cb(e));
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("openScreencast", () => {
  it("uses the global WebSocket constructor when one is not injected", () => {
    const instances: FakeWS[] = [];
    vi.stubGlobal(
      "WebSocket",
      function () {
        const ws = new FakeWS();
        instances.push(ws);
        return ws;
      },
    );
    openScreencast("ws://x/screencast", { onFrame: () => {} });
    expect(instances).toHaveLength(1);
  });

  it("sets binaryType to blob", () => {
    const ws = new FakeWS();
    openScreencast("ws://x/screencast", {
      onFrame: () => {},
      webSocketCtor: function () {
        return ws;
      } as unknown as typeof WebSocket,
    });
    expect(ws.binaryType).toBe("blob");
  });

  it("delivers binary frames as Blobs", () => {
    const ws = new FakeWS();
    const frames: Blob[] = [];
    openScreencast("ws://x/screencast", {
      onFrame: (b) => frames.push(b),
      webSocketCtor: function () {
        return ws;
      } as unknown as typeof WebSocket,
    });
    ws.emit("message", { data: new Blob([new Uint8Array([1, 2, 3])]) });
    expect(frames.length).toBe(1);
  });

  it("ignores non-Blob messages", () => {
    const ws = new FakeWS();
    const onFrame = vi.fn();
    openScreencast("ws://x/screencast", {
      onFrame,
      webSocketCtor: function () {
        return ws;
      } as unknown as typeof WebSocket,
    });
    ws.emit("message", { data: new Uint8Array([1, 2, 3]) });
    expect(onFrame).not.toHaveBeenCalled();
  });

  it("close() closes the socket", () => {
    const ws = new FakeWS();
    const handle = openScreencast("ws://x", {
      onFrame: () => {},
      webSocketCtor: function () {
        return ws;
      } as unknown as typeof WebSocket,
    });
    handle.close();
    expect(ws.close).toHaveBeenCalled();
  });

  it("close() does not throw if socket close throws", () => {
    const ws = new FakeWS();
    ws.close.mockImplementation(() => {
      throw new Error("close failed");
    });
    const handle = openScreencast("ws://x", {
      onFrame: () => {},
      webSocketCtor: function () {
        return ws;
      } as unknown as typeof WebSocket,
    });
    expect(() => handle.close()).not.toThrow();
  });

  it("forwards error and close callbacks", () => {
    const ws = new FakeWS();
    const onError = vi.fn();
    const onClose = vi.fn();
    openScreencast("ws://x", {
      onFrame: () => {},
      onError,
      onClose,
      webSocketCtor: function () {
        return ws;
      } as unknown as typeof WebSocket,
    });
    ws.emit("error", new Event("error"));
    ws.emit("close", { code: 1000, reason: "ok", wasClean: true });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
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
