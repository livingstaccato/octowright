export type FullscreenMode = "native" | "panel";

export interface FullscreenController {
  toggle(): void;
  isActive(): boolean;
  destroy(): void;
}

const MAXIMIZED_CLASS = "live-preview--maximized";

export function attachFullscreen(
  button: HTMLElement,
  target: HTMLElement,
  mode: FullscreenMode,
): FullscreenController {
  let disposed = false;
  let nativeRequestGeneration = 0;

  const panelActive = (): boolean => target.classList.contains(MAXIMIZED_CLASS);
  const nativeActive = (): boolean => document.fullscreenElement === target;
  const isActive = (): boolean => !disposed && (panelActive() || nativeActive());

  const enterPanel = (): void => {
    if (disposed) return;
    target.classList.add(MAXIMIZED_CLASS);
  };

  const exitPanel = (): void => {
    target.classList.remove(MAXIMIZED_CLASS);
  };

  const togglePanel = (): void => {
    if (disposed) return;
    target.classList.toggle(MAXIMIZED_CLASS);
  };

  const enterNative = (): void => {
    if (disposed) return;
    nativeRequestGeneration += 1;
    const generation = nativeRequestGeneration;
    const requestFullscreen = target.requestFullscreen;
    if (!requestFullscreen) {
      enterPanel();
      return;
    }

    try {
      Promise.resolve(requestFullscreen.call(target)).catch(() => {
        if (disposed || generation !== nativeRequestGeneration) return;
        enterPanel();
      });
    } catch {
      enterPanel();
    }
  };

  const exitNative = (): void => {
    if (!document.exitFullscreen) return;
    try {
      void Promise.resolve(document.exitFullscreen()).catch(() => {
        // Best-effort cleanup path: a rejected fullscreen exit should not
        // break preview teardown or leave event listeners registered.
      });
    } catch {
      // Ignore synchronous browser/fullscreen API failures during cleanup.
    }
  };

  const toggle = (): void => {
    if (disposed) return;
    if (panelActive()) {
      exitPanel();
      return;
    }

    if (mode === "panel") {
      togglePanel();
      return;
    }

    if (nativeActive()) {
      exitNative();
      return;
    }

    enterNative();
  };

  const onKeyDown = (event: KeyboardEvent): void => {
    if (disposed) return;
    if (event.key === "Escape") exitPanel();
  };

  const destroy = (): void => {
    if (disposed) return;
    const shouldExitNative = nativeActive();
    disposed = true;
    nativeRequestGeneration += 1;
    button.removeEventListener("click", toggle);
    document.removeEventListener("keydown", onKeyDown);
    exitPanel();
    if (shouldExitNative) exitNative();
  };

  button.addEventListener("click", toggle);
  document.addEventListener("keydown", onKeyDown);

  return { toggle, isActive, destroy };
}
