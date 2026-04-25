import { describe, expect, it, vi } from "vitest";
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

describe("openTail", () => {
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
  it("close() invokes WebSocket.close", () => {
    FakeWebSocket.instances = [];
    const handle = openTail("ws://test/x", {
      onMessage: () => {},
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    handle.close();
    expect(FakeWebSocket.instances[0]!.closed).toBe(true);
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
});
