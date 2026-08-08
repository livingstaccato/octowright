// Screenshot-fallback polling behaviour: one request at a time, backoff on
// failure, and a timestamp that only moves when a frame actually loaded.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mountLivePreview } from "./live-preview.js";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  binaryType: BinaryType = "arraybuffer";
  close = vi.fn(() => {
    this.emitClose(1000, "closed", true);
  });
  readonly listeners = new Map<string, EventListener[]>();

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emitClose(code = 1011, reason = "screencast unavailable; use fallback", wasClean = false): void {
    const event = new Event("close") as CloseEvent;
    Object.defineProperties(event, {
      code: { value: code },
      reason: { value: reason },
      wasClean: { value: wasClean },
    });
    for (const listener of this.listeners.get("close") ?? []) listener(event);
  }
}

let container: HTMLDivElement;

beforeEach(() => {
  FakeWebSocket.instances = [];
  container = document.createElement("div");
  document.body.append(container);
  vi.useFakeTimers();
});

afterEach(() => {
  container.remove();
  vi.useRealTimers();
});

function mountAndDropToFallback(sessionId = "fb", intervalMs = 1000) {
  const handle = mountLivePreview(container, {
    sessionId,
    isLive: true,
    intervalMs,
    webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
  });
  handle.start();
  const ws = FakeWebSocket.instances[0];
  const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
  const timestamp = container.querySelector<HTMLElement>('[data-testid="live-preview-timestamp"]');
  if (!ws || !img || !timestamp) throw new Error("missing preview elements");
  ws.emitClose();
  return { handle, img, timestamp };
}

describe("live preview screenshot fallback", () => {
  it("keeps one screenshot request in flight at a time", async () => {
    const { handle, img } = mountAndDropToFallback("fb-inflight");
    const firstSrc = img.src;
    expect(firstSrc).toContain("/api/sessions/fb-inflight/screenshot/now");

    // The first request never resolves: later ticks must not replace src.
    await vi.advanceTimersByTimeAsync(5000);
    expect(img.src).toBe(firstSrc);

    img.dispatchEvent(new Event("load"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(img.src).not.toBe(firstSrc);

    handle.destroy();
  });

  it("advances the timestamp only after a frame loads", async () => {
    const { handle, img, timestamp } = mountAndDropToFallback("fb-timestamp");
    expect(timestamp.textContent).toBe("—");

    img.dispatchEvent(new Event("load"));
    expect(timestamp.textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);

    handle.destroy();
  });

  it("backs off after failures and recovers the base cadence on success", async () => {
    const { handle, img } = mountAndDropToFallback("fb-backoff", 1000);
    const firstSrc = img.src;

    img.dispatchEvent(new Event("error"));
    // Backed off to 2× the base interval — nothing at 1s.
    await vi.advanceTimersByTimeAsync(1000);
    expect(img.src).toBe(firstSrc);
    await vi.advanceTimersByTimeAsync(1000);
    const secondSrc = img.src;
    expect(secondSrc).not.toBe(firstSrc);

    img.dispatchEvent(new Event("error"));
    // Second consecutive failure: 4× the base interval.
    await vi.advanceTimersByTimeAsync(3000);
    expect(img.src).toBe(secondSrc);
    await vi.advanceTimersByTimeAsync(1000);
    const thirdSrc = img.src;
    expect(thirdSrc).not.toBe(secondSrc);

    // A success resets the backoff to the base interval.
    img.dispatchEvent(new Event("load"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(img.src).not.toBe(thirdSrc);

    handle.destroy();
  });

  it("stops polling and detaches image listeners once paused", async () => {
    const { handle, img } = mountAndDropToFallback("fb-paused");
    img.dispatchEvent(new Event("load"));
    handle.stop();
    const pausedSrc = img.src;

    await vi.advanceTimersByTimeAsync(10_000);
    expect(img.src).toBe(pausedSrc);

    handle.destroy();
  });
});
