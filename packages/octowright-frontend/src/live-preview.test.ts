import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mountLivePreview } from "./live-preview.js";

let container: HTMLDivElement;
beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("mountLivePreview — closed session", () => {
  it("renders the closed-state placeholder and never polls", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "abc", isLive: false });
    const placeholder = container.querySelector('[data-testid="live-preview-placeholder"]');
    expect(placeholder).not.toBeNull();
    expect(placeholder?.textContent).toMatch(/closed/i);

    // No <img> rendered for closed sessions.
    expect(container.querySelector('[data-testid="live-preview-img"]')).toBeNull();

    // start() must be a no-op — calling it should not create timers.
    handle.start();
    handle.stop();
    handle.setInterval(1000);
    vi.advanceTimersByTime(60_000);
    expect(container.querySelector('[data-testid="live-preview-img"]')).toBeNull();
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
  it("renders toolbar + img element", () => {
    const handle = mountLivePreview(container, { sessionId: "live1", isLive: true });
    expect(container.querySelector('[data-testid="live-preview-img"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="live-preview-play"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="live-preview-rate"]')).not.toBeNull();
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

  it("on start() the <img>.src updates with cache-busted URL", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live2", isLive: true, intervalMs: 5000 });
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    if (!img) throw new Error("img missing");
    handle.start();
    // Initial tick fires synchronously inside start().
    expect(img.src).toContain("/api/sessions/live2/screenshot/now");
    expect(img.src).toMatch(/[?&]_=\d+/);
    handle.destroy();
  });

  it("uses Date.now/performance fallback path when load completes without performance.now", () => {
    vi.useFakeTimers();
    vi.stubGlobal("performance", undefined);
    const handle = mountLivePreview(container, { sessionId: "live-no-perf", isLive: true });
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    const timestamp = container.querySelector('[data-testid="live-preview-timestamp"]');
    if (!img || !timestamp) throw new Error("missing preview elements");

    handle.start();
    img.dispatchEvent(new Event("load"));

    expect(timestamp.textContent).not.toBe("—");
    handle.destroy();
  });

  it("stop() halts polling and start() resumes", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live3", isLive: true, intervalMs: 1000 });
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    if (!img) throw new Error("img missing");
    handle.start();
    const firstSrc = img.src;
    expect(firstSrc).toContain("screenshot/now");
    handle.stop();
    // After stop, advancing time should not change src.
    vi.advanceTimersByTime(5000);
    expect(img.src).toBe(firstSrc);
    // After start, the immediate tick changes src (cache-bust differs).
    vi.advanceTimersByTime(1); // tick the timestamp
    handle.start();
    expect(img.src).toContain("screenshot/now");
    handle.destroy();
  });

  it("start and stop are idempotent while already in the target state", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live3b", isLive: true, intervalMs: 1000 });
    handle.start();
    const timerCount = vi.getTimerCount();
    handle.start();
    expect(vi.getTimerCount()).toBe(timerCount);
    handle.stop();
    handle.stop();
    expect(vi.getTimerCount()).toBe(0);
    handle.destroy();
  });

  it("setInterval(ms) changes tick rate", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live4", isLive: true, intervalMs: 5000 });
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    if (!img) throw new Error("img missing");
    handle.start();
    img.dispatchEvent(new Event("load")); // release the initial tick's inflight guard
    const initialSrc = img.src;
    // Switch to 1000ms; advance 1100ms — should have ticked.
    handle.setInterval(1000);
    vi.advanceTimersByTime(1100);
    expect(img.src).not.toBe(initialSrc);
    // Rate select reflects the new value.
    const rate = container.querySelector<HTMLSelectElement>('[data-testid="live-preview-rate"]');
    expect(rate?.value).toBe("1000");
    handle.destroy();
  });

  it("setInterval before start updates the selector without creating a timer", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live4b", isLive: true, intervalMs: 3000 });
    handle.setInterval(10000);
    const rate = container.querySelector<HTMLSelectElement>('[data-testid="live-preview-rate"]');
    expect(rate?.value).toBe("10000");
    expect(vi.getTimerCount()).toBe(0);
    handle.destroy();
  });

  it("destroy() removes DOM and cancels pending interval", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live5", isLive: true, intervalMs: 1000 });
    handle.start();
    expect(container.querySelector('[data-testid="live-preview-img"]')).not.toBeNull();
    handle.destroy();
    expect(container.querySelector('[data-testid="live-preview-img"]')).toBeNull();
    // Advancing time after destroy must be safe and not re-create the img.
    vi.advanceTimersByTime(60_000);
    expect(container.querySelector('[data-testid="live-preview-img"]')).toBeNull();
  });

  it("badge transitions LIVE → PAUSED on stop, back to LIVE on start", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live6", isLive: true });
    handle.start();
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    expect(badge?.textContent).toBe("LIVE");
    handle.stop();
    expect(badge?.textContent).toBe("PAUSED");
    handle.start();
    expect(badge?.textContent).toBe("LIVE");
    handle.destroy();
  });

  it("rate selector dropdown change calls setInterval with new value", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live7", isLive: true, intervalMs: 3000 });
    handle.start();
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    if (!img) throw new Error("img missing");
    img.dispatchEvent(new Event("load")); // release the initial tick's inflight guard
    const rate = container.querySelector<HTMLSelectElement>('[data-testid="live-preview-rate"]');
    if (!rate) throw new Error("rate select missing");
    rate.value = "10000";
    rate.dispatchEvent(new Event("change"));
    // Internal state followed: a 1500ms tick now should NOT update src.
    const before = img.src;
    vi.advanceTimersByTime(1500);
    expect(img.src).toBe(before);
    // But 10000ms+ should.
    vi.advanceTimersByTime(9000);
    expect(img.src).not.toBe(before);
    handle.destroy();
  });

  it("ignores invalid rate selector values", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live7b", isLive: true, intervalMs: 3000 });
    handle.start();
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    const rate = container.querySelector<HTMLSelectElement>('[data-testid="live-preview-rate"]');
    if (!img || !rate) throw new Error("missing preview controls");
    img.dispatchEvent(new Event("load"));
    rate.value = "";
    rate.dispatchEvent(new Event("change"));
    const before = img.src;
    vi.advanceTimersByTime(1000);
    expect(img.src).toBe(before);
    handle.destroy();
  });

  it("play button toggles polling state", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live8", isLive: true });
    handle.start();
    const playBtn = container.querySelector<HTMLButtonElement>('[data-testid="live-preview-play"]');
    if (!playBtn) throw new Error("play button missing");
    expect(playBtn.textContent).toBe("⏸");
    playBtn.click();
    expect(playBtn.textContent).toBe("▶");
    playBtn.click();
    expect(playBtn.textContent).toBe("⏸");
    handle.destroy();
  });

  it("markClosed stops polling, swaps to CLOSED badge, and disables play", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live9", isLive: true, intervalMs: 1000 });
    handle.start();
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    const playBtn = container.querySelector<HTMLButtonElement>('[data-testid="live-preview-play"]');
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    if (!img || !playBtn || !badge) throw new Error("missing elements");

    handle.markClosed();
    const srcAfterClose = img.src;
    // Advance well past several poll intervals — img.src must not change again.
    vi.advanceTimersByTime(60_000);
    expect(img.src).toBe(srcAfterClose);
    expect(badge.textContent).toBe("CLOSED");
    expect(playBtn.disabled).toBe(true);
    handle.destroy();
  });

  it("markClosed clears a visible error indicator", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live9b", isLive: true, intervalMs: 1000 });
    handle.start();
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    const error = container.querySelector<HTMLElement>('[data-testid="live-preview-error"]');
    if (!img || !error) throw new Error("missing preview elements");

    img.dispatchEvent(new Event("error"));
    expect(error.style.display).toBe("");
    handle.markClosed();
    expect(error.style.display).toBe("none");
    handle.destroy();
  });

  it("markClosed is idempotent and a no-op for already-closed sessions", () => {
    const closedHandle = mountLivePreview(container, { sessionId: "closed1", isLive: false });
    // Should not throw — closed-mode handle exposes a no-op markClosed.
    closedHandle.markClosed();
    closedHandle.markClosed();
    closedHandle.destroy();
  });

  it("backs off poll interval after repeated screenshot errors", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live10", isLive: true, intervalMs: 1000 });
    handle.start();
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    if (!img) throw new Error("img missing");

    img.dispatchEvent(new Event("error"));
    img.dispatchEvent(new Event("error"));

    const before = img.src;
    vi.advanceTimersByTime(1_000);
    expect(img.src).toBe(before);
    vi.advanceTimersByTime(1_100);
    expect(img.src).not.toBe(before);
    handle.destroy();
  });

  it("handles an image error after polling has already been stopped", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live10b", isLive: true, intervalMs: 1000 });
    handle.start();
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    const error = container.querySelector<HTMLElement>('[data-testid="live-preview-error"]');
    if (!img || !error) throw new Error("missing preview elements");

    handle.stop();
    img.dispatchEvent(new Event("error"));

    expect(error.textContent).toContain("transient error");
    expect(vi.getTimerCount()).toBe(0);
    handle.destroy();
  });

  it("returns to base interval quickly after first success", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live11", isLive: true, intervalMs: 1000 });
    handle.start();
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    if (!img) throw new Error("img missing");

    img.dispatchEvent(new Event("error"));
    vi.advanceTimersByTime(2_100);
    img.dispatchEvent(new Event("load"));

    const before = img.src;
    vi.advanceTimersByTime(1_100);
    expect(img.src).not.toBe(before);
    handle.destroy();
  });

  it("caps adaptive backoff at the maximum interval", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live12", isLive: true, intervalMs: 1000 });
    handle.start();
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    if (!img) throw new Error("img missing");

    for (let i = 0; i < 5; i++) {
      img.dispatchEvent(new Event("error"));
      vi.advanceTimersByTime(10_100);
    }
    // Resolve the trailing inflight tick with one more error so backoff stays
    // capped (a load would reset to the base interval). This puts us in a
    // clean state where the next interval-driven tick can run.
    img.dispatchEvent(new Event("error"));

    const before = img.src;
    vi.advanceTimersByTime(9_000);
    expect(img.src).toBe(before);
    vi.advanceTimersByTime(1_100);
    expect(img.src).not.toBe(before);
    handle.destroy();
  });

  it("skips ticks while a fetch is still in flight (no listener stacking)", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live13", isLive: true, intervalMs: 100 });
    handle.start();
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    if (!img) throw new Error("img missing");

    const initialSrc = img.src;
    // Multiple tick intervals fire before the first load resolves.
    vi.advanceTimersByTime(500);
    // src never changes again because tick() short-circuits while inflight.
    expect(img.src).toBe(initialSrc);

    // Resolving the in-flight load lets a subsequent tick proceed.
    img.dispatchEvent(new Event("load"));
    vi.advanceTimersByTime(150);
    expect(img.src).not.toBe(initialSrc);
    handle.destroy();
  });

  it("live handle methods are no-ops after destroy", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live14", isLive: true, intervalMs: 1000 });
    handle.start();
    handle.destroy();

    expect(() => {
      handle.start();
      handle.stop();
      handle.setInterval(1000);
      handle.markClosed();
    }).not.toThrow();
    expect(container.querySelector('[data-testid="live-preview-img"]')).toBeNull();
  });

  it("destroy before start removes DOM without clearing a timer", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live15", isLive: true, intervalMs: 1000 });
    handle.destroy();
    expect(vi.getTimerCount()).toBe(0);
    expect(container.classList.contains("live-preview")).toBe(false);
  });
});
