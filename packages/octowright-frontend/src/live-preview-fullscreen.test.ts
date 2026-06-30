import { describe, expect, it, vi } from "vitest";
import { attachFullscreen } from "./live-preview-fullscreen.js";

describe("attachFullscreen", () => {
  it("panel mode toggles the maximized class", () => {
    const btn = document.createElement("button");
    const target = document.createElement("div");
    const ctrl = attachFullscreen(btn, target, "panel");
    ctrl.toggle();
    expect(target.classList.contains("live-preview--maximized")).toBe(true);
    ctrl.toggle();
    expect(target.classList.contains("live-preview--maximized")).toBe(false);
    ctrl.destroy();
  });

  it("button click toggles the current mode", () => {
    const btn = document.createElement("button");
    const target = document.createElement("div");
    const ctrl = attachFullscreen(btn, target, "panel");
    btn.click();
    expect(target.classList.contains("live-preview--maximized")).toBe(true);
    btn.click();
    expect(target.classList.contains("live-preview--maximized")).toBe(false);
    ctrl.destroy();
  });

  it("native mode calls requestFullscreen", () => {
    const btn = document.createElement("button");
    const target = document.createElement("div");
    const req = vi.fn().mockResolvedValue(undefined);
    (target as unknown as { requestFullscreen: () => Promise<void> }).requestFullscreen = req;
    const ctrl = attachFullscreen(btn, target, "native");
    ctrl.toggle();
    expect(req).toHaveBeenCalled();
    ctrl.destroy();
  });

  it("native falls back to panel when requestFullscreen is absent", () => {
    const btn = document.createElement("button");
    const target = document.createElement("div");
    const ctrl = attachFullscreen(btn, target, "native");
    ctrl.toggle();
    expect(target.classList.contains("live-preview--maximized")).toBe(true);
    ctrl.destroy();
  });

  it("native falls back to panel when requestFullscreen rejects", async () => {
    const btn = document.createElement("button");
    const target = document.createElement("div");
    const req = vi.fn().mockRejectedValue(new Error("denied"));
    (target as unknown as { requestFullscreen: () => Promise<void> }).requestFullscreen = req;
    const ctrl = attachFullscreen(btn, target, "native");
    ctrl.toggle();
    await Promise.resolve();
    expect(target.classList.contains("live-preview--maximized")).toBe(true);
    ctrl.destroy();
  });

  it("destroy removes listeners and clears panel state", () => {
    const btn = document.createElement("button");
    const target = document.createElement("div");
    const ctrl = attachFullscreen(btn, target, "panel");
    ctrl.toggle();
    ctrl.destroy();
    expect(ctrl.isActive()).toBe(false);
    expect(target.classList.contains("live-preview--maximized")).toBe(false);

    btn.click();
    expect(target.classList.contains("live-preview--maximized")).toBe(false);

    target.classList.add("live-preview--maximized");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(target.classList.contains("live-preview--maximized")).toBe(true);
  });

  it("destroy exits native fullscreen when the target is active", () => {
    const btn = document.createElement("button");
    const target = document.createElement("div");
    const exitFullscreen = vi.fn().mockRejectedValue(new Error("ignored"));
    const fullscreenDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, "fullscreenElement");
    const exitDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, "exitFullscreen");
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      value: target,
    });
    Object.defineProperty(document, "exitFullscreen", {
      configurable: true,
      value: exitFullscreen,
    });

    try {
      const ctrl = attachFullscreen(btn, target, "native");
      ctrl.destroy();

      expect(exitFullscreen).toHaveBeenCalled();
    } finally {
      if (fullscreenDescriptor) {
        Object.defineProperty(document, "fullscreenElement", fullscreenDescriptor);
      } else {
        delete (document as unknown as { fullscreenElement?: Element | null }).fullscreenElement;
      }
      if (exitDescriptor) {
        Object.defineProperty(document, "exitFullscreen", exitDescriptor);
      } else {
        delete (document as unknown as { exitFullscreen?: () => Promise<void> }).exitFullscreen;
      }
    }
  });

  it("does not fall back to panel after a stale native rejection", async () => {
    const btn = document.createElement("button");
    const target = document.createElement("div");
    let rejectRequest!: (err: Error) => void;
    const req = vi.fn(
      () =>
        new Promise<void>((_, reject) => {
          rejectRequest = reject;
        }),
    );
    (target as unknown as { requestFullscreen: () => Promise<void> }).requestFullscreen = req;
    const ctrl = attachFullscreen(btn, target, "native");
    ctrl.toggle();
    ctrl.destroy();
    rejectRequest(new Error("denied"));
    await Promise.resolve();
    expect(target.classList.contains("live-preview--maximized")).toBe(false);
  });

  it("Escape exits panel mode", () => {
    const btn = document.createElement("button");
    const target = document.createElement("div");
    const ctrl = attachFullscreen(btn, target, "panel");
    ctrl.toggle();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(ctrl.isActive()).toBe(false);
    expect(target.classList.contains("live-preview--maximized")).toBe(false);
    ctrl.destroy();
  });
});
