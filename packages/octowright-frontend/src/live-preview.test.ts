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

  emitMessage(data: Blob | Uint8Array): void {
    this.emit(new MessageEvent("message", { data }));
  }

  emitError(): void {
    this.emit(new Event("error"));
  }

  emitClose(code = 1006, reason = "lost", wasClean = false): void {
    const event = new Event("close") as CloseEvent;
    Object.defineProperties(event, {
      code: { value: code },
      reason: { value: reason },
      wasClean: { value: wasClean },
    });
    this.emit(event);
  }

  private emit(event: Event): void {
    for (const listener of this.listeners.get(event.type) ?? []) {
      listener(event);
    }
  }
}

let container: HTMLDivElement;
let objectUrlCount = 0;

beforeEach(() => {
  FakeWebSocket.instances = [];
  objectUrlCount = 0;
  container = document.createElement("div");
  document.body.append(container);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => {
      objectUrlCount += 1;
      return `blob:frame-${objectUrlCount}`;
    }),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  container.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function mountLive(sessionId = "live1") {
  return mountLivePreview(container, {
    sessionId,
    isLive: true,
    fps: 7,
    webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
  });
}

describe("mountLivePreview — closed session", () => {
  it("renders the closed-state placeholder and never opens a stream", () => {
    const handle = mountLivePreview(container, {
      sessionId: "abc",
      isLive: false,
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    const placeholder = container.querySelector('[data-testid="live-preview-placeholder"]');
    expect(placeholder).not.toBeNull();
    expect(placeholder?.textContent).toMatch(/closed/i);

    expect(container.querySelector('[data-testid="live-preview-img"]')).toBeNull();

    handle.start();
    handle.stop();
    handle.setInterval(1000);
    expect(FakeWebSocket.instances).toHaveLength(0);
    handle.destroy();
  });

  it("shows CLOSED badge for closed sessions", () => {
    mountLivePreview(container, { sessionId: "abc", isLive: false });
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    expect(badge?.textContent).toBe("CLOSED");
  });

  it("does not render fullscreen controls for closed sessions", () => {
    const handle = mountLivePreview(container, { sessionId: "abc", isLive: false });
    expect(container.querySelector('[data-testid="live-preview-fullscreen"]')).toBeNull();
    handle.destroy();
  });
});

describe("mountLivePreview — live session", () => {
  it("renders toolbar + img element without a polling-rate selector", () => {
    const handle = mountLive();
    expect(container.querySelector('[data-testid="live-preview-img"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="live-preview-play"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="live-preview-rate"]')).toBeNull();
    expect(container.querySelector('[data-testid="live-preview-timestamp"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="live-preview-badge"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="live-preview-fullscreen"]')).not.toBeNull();
    handle.destroy();
  });

  it("fullscreen button toggles panel mode when configured", () => {
    const handle = mountLivePreview(container, {
      sessionId: "live-fullscreen-panel",
      isLive: true,
      fullscreenMode: "panel",
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    const fullscreenBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="live-preview-fullscreen"]',
    );
    if (!fullscreenBtn) throw new Error("fullscreen button missing");

    fullscreenBtn.click();
    expect(container.classList.contains("live-preview--maximized")).toBe(true);
    fullscreenBtn.click();
    expect(container.classList.contains("live-preview--maximized")).toBe(false);
    handle.destroy();
  });

  it("destroy cleans up fullscreen controller state and listeners", () => {
    const handle = mountLivePreview(container, {
      sessionId: "live-fullscreen-destroy",
      isLive: true,
      fullscreenMode: "panel",
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    const fullscreenBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="live-preview-fullscreen"]',
    );
    if (!fullscreenBtn) throw new Error("fullscreen button missing");

    fullscreenBtn.click();
    handle.destroy();
    expect(container.classList.contains("live-preview--maximized")).toBe(false);

    fullscreenBtn.click();
    expect(container.classList.contains("live-preview--maximized")).toBe(false);
  });

  it("markClosed disables fullscreen and prevents stale native fallback", async () => {
    let rejectRequest!: (err: Error) => void;
    const req = vi.fn(
      () =>
        new Promise<void>((_, reject) => {
          rejectRequest = reject;
        }),
    );
    (container as unknown as { requestFullscreen: () => Promise<void> }).requestFullscreen = req;
    const handle = mountLivePreview(container, {
      sessionId: "live-fullscreen-mark-closed",
      isLive: true,
      fullscreenMode: "native",
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    const fullscreenBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="live-preview-fullscreen"]',
    );
    if (!fullscreenBtn) throw new Error("fullscreen button missing");

    fullscreenBtn.click();
    handle.markClosed();
    expect(fullscreenBtn.disabled).toBe(true);
    expect(container.classList.contains("live-preview--maximized")).toBe(false);

    rejectRequest(new Error("denied"));
    await Promise.resolve();
    expect(container.classList.contains("live-preview--maximized")).toBe(false);

    fullscreenBtn.click();
    expect(container.classList.contains("live-preview--maximized")).toBe(false);
    handle.destroy();
  });

  it("start opens one screencast WebSocket using backend fps", () => {
    const handle = mountLive("live2");
    handle.start();
    handle.start();

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0]?.url).toContain("/api/sessions/live2/screencast?fps=7");
    expect(FakeWebSocket.instances[0]?.binaryType).toBe("blob");
    handle.destroy();
  });

  it("frame updates img src, timestamp, badge, and revokes the previous frame URL", () => {
    const handle = mountLive("live-frame");
    handle.start();
    const ws = FakeWebSocket.instances[0];
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    const timestamp = container.querySelector('[data-testid="live-preview-timestamp"]');
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    if (!ws || !img || !timestamp || !badge) throw new Error("missing preview elements");

    ws.emitMessage(new Blob([new Uint8Array([1])]));
    expect(img.src).toBe("blob:frame-1");
    expect(timestamp.textContent).not.toBe("—");
    expect(badge.textContent).toBe("LIVE");

    ws.emitMessage(new Blob([new Uint8Array([2])]));
    expect(img.src).toBe("blob:frame-2");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:frame-1");
    handle.destroy();
  });

  it("stop closes the active stream and shows PAUSED without an error", () => {
    const handle = mountLive("live3");
    handle.start();
    const ws = FakeWebSocket.instances[0];
    const playBtn = container.querySelector<HTMLButtonElement>('[data-testid="live-preview-play"]');
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    const error = container.querySelector<HTMLElement>('[data-testid="live-preview-error"]');
    if (!ws || !playBtn || !badge || !error) throw new Error("missing preview elements");

    handle.stop();

    expect(ws.close).toHaveBeenCalledTimes(1);
    expect(playBtn.textContent).toBe("▶");
    expect(badge.textContent).toBe("PAUSED");
    expect(error.style.display).toBe("none");
    handle.destroy();
  });

  it("ignores late message, error, and close callbacks from a paused stream", () => {
    const handle = mountLive("live-stale-after-stop");
    handle.start();
    const ws = FakeWebSocket.instances[0];
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    const playBtn = container.querySelector<HTMLButtonElement>('[data-testid="live-preview-play"]');
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    const timestamp = container.querySelector('[data-testid="live-preview-timestamp"]');
    const error = container.querySelector<HTMLElement>('[data-testid="live-preview-error"]');
    if (!ws || !img || !playBtn || !badge || !timestamp || !error) {
      throw new Error("missing preview elements");
    }

    ws.emitMessage(new Blob([new Uint8Array([1])]));
    const imgSrcAfterFrame = img.src;
    expect(imgSrcAfterFrame).toBe("blob:frame-1");

    handle.stop();
    const pausedState = {
      imgSrc: img.src,
      badgeText: badge.textContent,
      playText: playBtn.textContent,
      timestampText: timestamp.textContent,
      errorDisplay: error.style.display,
      errorText: error.textContent,
    };

    ws.emitMessage(new Blob([new Uint8Array([2])]));
    ws.emitError();
    ws.emitClose(1006, "late close", false);

    expect(img.src).toBe(pausedState.imgSrc);
    expect(badge.textContent).toBe(pausedState.badgeText);
    expect(playBtn.textContent).toBe(pausedState.playText);
    expect(timestamp.textContent).toBe(pausedState.timestampText);
    expect(error.style.display).toBe(pausedState.errorDisplay);
    expect(error.textContent).toBe(pausedState.errorText);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    handle.destroy();
  });

  it("start after stop opens a fresh stream", () => {
    const handle = mountLive("live4");
    handle.start();
    const first = FakeWebSocket.instances[0];
    handle.stop();
    handle.start();

    expect(first?.close).toHaveBeenCalledTimes(1);
    expect(FakeWebSocket.instances).toHaveLength(2);
    handle.destroy();
  });

  it("play button toggles stream state", () => {
    const handle = mountLive("live5");
    handle.start();
    const playBtn = container.querySelector<HTMLButtonElement>('[data-testid="live-preview-play"]');
    if (!playBtn) throw new Error("play button missing");
    expect(playBtn.textContent).toBe("⏸");

    playBtn.click();
    expect(playBtn.textContent).toBe("▶");
    playBtn.click();
    expect(playBtn.textContent).toBe("⏸");
    expect(FakeWebSocket.instances).toHaveLength(2);
    handle.destroy();
  });

  it("unexpected stream error and close start fallback while keeping controls usable", () => {
    const handle = mountLive("live-error");
    handle.start();
    const ws = FakeWebSocket.instances[0];
    const playBtn = container.querySelector<HTMLButtonElement>('[data-testid="live-preview-play"]');
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    const error = container.querySelector<HTMLElement>('[data-testid="live-preview-error"]');
    if (!ws || !playBtn || !badge || !error) throw new Error("missing preview elements");

    ws.emitError();
    expect(error.textContent).toContain("stream error");
    ws.emitClose(1006, "abnormal", false);
    expect(badge.textContent).toBe("LIVE");
    expect(error.textContent).toContain("screenshot fallback");
    expect(playBtn.disabled).toBe(false);

    handle.stop();
    expect(badge.textContent).toBe("PAUSED");
    handle.start();
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(error.style.display).toBe("none");
    handle.destroy();
  });

  it("falls back to screenshot polling after an unexpected screencast close", async () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, {
      sessionId: "live-fallback",
      isLive: true,
      fps: 7,
      intervalMs: 1200,
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });
    handle.start();
    const ws = FakeWebSocket.instances[0];
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    const error = container.querySelector<HTMLElement>('[data-testid="live-preview-error"]');
    if (!ws || !img || !badge || !error) throw new Error("missing preview elements");

    ws.emitClose(1011, "screencast unavailable; use fallback", false);
    expect(img.src).toContain("/api/sessions/live-fallback/screenshot/now?format=png");
    expect(error.textContent).toContain("screenshot fallback");
    expect(badge.textContent).toBe("LIVE");
    const firstSrc = img.src;

    await vi.advanceTimersByTimeAsync(1200);
    expect(img.src).toContain("/api/sessions/live-fallback/screenshot/now?format=png");
    expect(img.src).not.toBe(firstSrc);

    handle.destroy();
  });

  it("ignores late message and error callbacks after an unexpected close", () => {
    const handle = mountLive("live-stale-after-close");
    handle.start();
    const ws = FakeWebSocket.instances[0];
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    const playBtn = container.querySelector<HTMLButtonElement>('[data-testid="live-preview-play"]');
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    const timestamp = container.querySelector('[data-testid="live-preview-timestamp"]');
    const error = container.querySelector<HTMLElement>('[data-testid="live-preview-error"]');
    if (!ws || !img || !playBtn || !badge || !timestamp || !error) {
      throw new Error("missing preview elements");
    }

    ws.emitMessage(new Blob([new Uint8Array([1])]));
    expect(img.src).toBe("blob:frame-1");
    ws.emitClose(1006, "abnormal", false);
    const closedState = {
      imgSrc: img.src,
      badgeText: badge.textContent,
      playText: playBtn.textContent,
      timestampText: timestamp.textContent,
      errorDisplay: error.style.display,
      errorText: error.textContent,
    };

    ws.emitMessage(new Blob([new Uint8Array([2])]));
    ws.emitError();

    expect(img.src).toBe(closedState.imgSrc);
    expect(badge.textContent).toBe(closedState.badgeText);
    expect(playBtn.textContent).toBe(closedState.playText);
    expect(timestamp.textContent).toBe(closedState.timestampText);
    expect(error.style.display).toBe(closedState.errorDisplay);
    expect(error.textContent).toBe(closedState.errorText);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    handle.destroy();
  });

  it("markClosed closes stream, disables controls, clears errors, and revokes the current frame URL", () => {
    const handle = mountLive("live-closed");
    handle.start();
    const ws = FakeWebSocket.instances[0];
    const playBtn = container.querySelector<HTMLButtonElement>('[data-testid="live-preview-play"]');
    const fullscreenBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="live-preview-fullscreen"]',
    );
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    const error = container.querySelector<HTMLElement>('[data-testid="live-preview-error"]');
    if (!ws || !playBtn || !fullscreenBtn || !badge || !error) throw new Error("missing elements");

    ws.emitMessage(new Blob([new Uint8Array([1])]));
    ws.emitError();
    expect(error.style.display).toBe("");

    handle.markClosed();

    expect(ws.close).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:frame-1");
    expect(badge.textContent).toBe("CLOSED");
    expect(playBtn.disabled).toBe(true);
    expect(fullscreenBtn.disabled).toBe(true);
    expect(error.style.display).toBe("none");

    handle.start();
    expect(FakeWebSocket.instances).toHaveLength(1);
    handle.destroy();
  });

  it("ignores late message, error, and close callbacks after markClosed", () => {
    const handle = mountLive("live-stale-after-mark-closed");
    handle.start();
    const ws = FakeWebSocket.instances[0];
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    const playBtn = container.querySelector<HTMLButtonElement>('[data-testid="live-preview-play"]');
    const fullscreenBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="live-preview-fullscreen"]',
    );
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    const timestamp = container.querySelector('[data-testid="live-preview-timestamp"]');
    const error = container.querySelector<HTMLElement>('[data-testid="live-preview-error"]');
    if (!ws || !img || !playBtn || !fullscreenBtn || !badge || !timestamp || !error) {
      throw new Error("missing preview elements");
    }

    ws.emitMessage(new Blob([new Uint8Array([1])]));
    expect(img.src).toBe("blob:frame-1");

    handle.markClosed();
    const closedState = {
      imgSrc: img.src,
      badgeText: badge.textContent,
      playDisabled: playBtn.disabled,
      fullscreenDisabled: fullscreenBtn.disabled,
      timestampText: timestamp.textContent,
      errorDisplay: error.style.display,
      errorText: error.textContent,
    };

    ws.emitMessage(new Blob([new Uint8Array([2])]));
    ws.emitError();
    ws.emitClose(1006, "late close", false);

    expect(img.src).toBe(closedState.imgSrc);
    expect(badge.textContent).toBe("CLOSED");
    expect(badge.textContent).toBe(closedState.badgeText);
    expect(playBtn.disabled).toBe(true);
    expect(playBtn.disabled).toBe(closedState.playDisabled);
    expect(fullscreenBtn.disabled).toBe(true);
    expect(fullscreenBtn.disabled).toBe(closedState.fullscreenDisabled);
    expect(timestamp.textContent).toBe(closedState.timestampText);
    expect(error.style.display).toBe("none");
    expect(error.style.display).toBe(closedState.errorDisplay);
    expect(error.textContent).toBe(closedState.errorText);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    handle.destroy();
  });

  it("destroy closes stream, revokes current object URL, and removes DOM", () => {
    const handle = mountLive("live-destroy");
    handle.start();
    const ws = FakeWebSocket.instances[0];
    if (!ws) throw new Error("stream missing");
    ws.emitMessage(new Blob([new Uint8Array([1])]));

    handle.destroy();

    expect(ws.close).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:frame-1");
    expect(container.querySelector('[data-testid="live-preview-img"]')).toBeNull();
    expect(container.classList.contains("live-preview")).toBe(false);
  });

  it("ignores late message, error, and close callbacks after destroy", () => {
    const handle = mountLive("live-stale-after-destroy");
    handle.start();
    const ws = FakeWebSocket.instances[0];
    if (!ws) throw new Error("stream missing");

    handle.destroy();

    expect(() => {
      ws.emitMessage(new Blob([new Uint8Array([1])]));
      ws.emitError();
      ws.emitClose(1006, "late close", false);
    }).not.toThrow();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="live-preview-img"]')).toBeNull();
  });

  it("live handle methods are no-ops after destroy", () => {
    const handle = mountLive("live-noop");
    handle.start();
    handle.destroy();

    expect(() => {
      handle.start();
      handle.stop();
      handle.setInterval(1000);
      handle.markClosed();
    }).not.toThrow();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
