import { afterEach, describe, expect, it, vi } from "vitest";
import { openTail } from "./tail.js";

interface Listener {
  type: string;
  handler: (e: unknown) => void;
}

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  listeners: Listener[] = [];
  readyState = 1;
  closed = false;
  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }
  addEventListener(type: string, handler: (e: unknown) => void): void {
    this.listeners.push({ type, handler });
  }
  emit(type: string, event: unknown): void {
    for (const listener of this.listeners) {
      if (listener.type === type) listener.handler(event);
    }
  }
  close(): void {
    this.closed = true;
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("openTail", () => {
  it("uses the global WebSocket constructor when one is not injected", () => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    openTail("ws://test/global", { onMessage: () => {} });
    expect(FakeWebSocket.instances[0]?.url).toBe("ws://test/global");
  });

  it("handles open events", () => {
    FakeWebSocket.instances = [];
    openTail("ws://test/x", {
      onMessage: () => {},
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    expect(() => FakeWebSocket.instances[0]!.emit("open", new Event("open"))).not.toThrow();
  });

  it("invokes onMessage with parsed payload", () => {
    FakeWebSocket.instances = [];
    const onMessage = vi.fn();
    openTail("ws://test/api/sessions/x/tail", {
      onMessage,
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    const ws = FakeWebSocket.instances[0]!;
    ws.emit("message", {
      data: JSON.stringify({ events: [{ ts: "2026-04-24T13:00:00Z", action: "click" }], cursor: 12 }),
    });
    expect(onMessage).toHaveBeenCalledTimes(1);
    expect(onMessage.mock.calls[0]?.[0]?.cursor).toBe(12);
  });
  it("ignores malformed JSON", () => {
    FakeWebSocket.instances = [];
    const onMessage = vi.fn();
    openTail("ws://test/x", {
      onMessage,
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    FakeWebSocket.instances[0]!.emit("message", { data: "not json" });
    FakeWebSocket.instances[0]!.emit("message", { data: 1234 });
    expect(onMessage).not.toHaveBeenCalled();
  });
  it("ignores frames missing required fields", () => {
    FakeWebSocket.instances = [];
    const onMessage = vi.fn();
    openTail("ws://test/x", {
      onMessage,
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    FakeWebSocket.instances[0]!.emit("message", { data: JSON.stringify({ foo: 1 }) });
    expect(onMessage).not.toHaveBeenCalled();
  });
  it("accepts complete=true frames", () => {
    FakeWebSocket.instances = [];
    const onMessage = vi.fn();
    openTail("ws://test/x", {
      onMessage,
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    FakeWebSocket.instances[0]!.emit("message", {
      data: JSON.stringify({ events: [], cursor: 5, complete: true }),
    });
    expect(onMessage.mock.calls[0]?.[0]?.complete).toBe(true);
  });
  it("close() invokes WebSocket.close", () => {
    FakeWebSocket.instances = [];
    const handle = openTail("ws://test/x", {
      onMessage: () => {},
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    handle.close();
    expect(FakeWebSocket.instances[0]!.closed).toBe(true);
  });
  it("close() swallows WebSocket close errors", () => {
    class ThrowingWebSocket extends FakeWebSocket {
      override close(): void {
        throw new Error("close failed");
      }
    }
    FakeWebSocket.instances = [];
    const handle = openTail("ws://test/x", {
      onMessage: () => {},
      webSocketCtor: ThrowingWebSocket as unknown as typeof WebSocket,
    });
    expect(() => handle.close()).not.toThrow();
  });
  it("forwards onError and onClose if given", () => {
    FakeWebSocket.instances = [];
    const onError = vi.fn();
    const onClose = vi.fn();
    openTail("ws://test/x", {
      onMessage: () => {},
      onError,
      onClose,
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    const ws = FakeWebSocket.instances[0]!;
    ws.emit("error", new Event("error"));
    ws.emit("close", { code: 1000, reason: "ok" });
    expect(onError).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
  it("handles error and close events when optional callbacks are absent", () => {
    FakeWebSocket.instances = [];
    openTail("ws://test/x", {
      onMessage: () => {},
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    const ws = FakeWebSocket.instances[0]!;
    expect(() => {
      ws.emit("error", new Event("error"));
      ws.emit("close", { code: 1006, reason: "gone", wasClean: false });
    }).not.toThrow();
  });
});
