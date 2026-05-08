import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mountLivePreview } from "./live-preview.js";

let container: HTMLDivElement;
beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
});
afterEach(() => {
  vi.useRealTimers();
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
    vi.advanceTimersByTime(60_000);
    expect(container.querySelector('[data-testid="live-preview-img"]')).toBeNull();
    handle.destroy();
  });

  it("shows CLOSED badge for closed sessions", () => {
    mountLivePreview(container, { sessionId: "abc", isLive: false });
    const badge = container.querySelector('[data-testid="live-preview-badge"]');
    expect(badge?.textContent).toBe("CLOSED");
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

  it("setInterval(ms) changes tick rate", () => {
    vi.useFakeTimers();
    const handle = mountLivePreview(container, { sessionId: "live4", isLive: true, intervalMs: 5000 });
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    if (!img) throw new Error("img missing");
    handle.start();
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
    const rate = container.querySelector<HTMLSelectElement>('[data-testid="live-preview-rate"]');
    if (!rate) throw new Error("rate select missing");
    rate.value = "10000";
    rate.dispatchEvent(new Event("change"));
    // Internal state followed: a 1500ms tick now should NOT update src.
    const img = container.querySelector<HTMLImageElement>('[data-testid="live-preview-img"]');
    if (!img) throw new Error("img missing");
    const before = img.src;
    vi.advanceTimersByTime(1500);
    expect(img.src).toBe(before);
    // But 10000ms+ should.
    vi.advanceTimersByTime(9000);
    expect(img.src).not.toBe(before);
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

    const before = img.src;
    vi.advanceTimersByTime(9_000);
    expect(img.src).toBe(before);
    vi.advanceTimersByTime(1_100);
    expect(img.src).not.toBe(before);
    handle.destroy();
  });
});
