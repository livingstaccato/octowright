// Live preview panel: streams live browser frames over a single WebSocket.

import { liveScreenshotUrl, screencastWsUrl } from "./api.js";
import { attachFullscreen, type FullscreenMode } from "./live-preview-fullscreen.js";
import { openScreencast, type ScreencastHandle } from "./live-preview-screencast.js";
import { getLogger } from "./telemetry.js";

const log = getLogger("octowright.frontend.live-preview");

export interface LivePreviewOptions {
  sessionId: string;
  isLive: boolean;
  /** Server-side screencast FPS. The stream endpoint controls frame cadence. */
  fps?: number;
  /** Default 'native'. Falls back to panel mode when native fullscreen is unavailable. */
  fullscreenMode?: FullscreenMode;
  /** Inject a WebSocket constructor for tests. */
  webSocketCtor?: typeof WebSocket;
  /** Deprecated compatibility option ignored by the screencast stream. */
  intervalMs?: number;
  /** Deprecated compatibility option ignored by the screencast stream. */
  format?: "png" | "jpeg";
}

export interface LivePreviewHandle {
  start: () => void;
  stop: () => void;
  setInterval: (ms: number) => void;
  /** Transition a previously-live preview to its closed state. Idempotent. */
  markClosed: () => void;
  destroy: () => void;
}

interface InternalState {
  stream: ScreencastHandle | null;
  fallbackTimer: ReturnType<typeof setInterval> | null;
  generation: number;
  expectedCloseGeneration: number | null;
  destroyed: boolean;
  closed: boolean;
  objectUrl: string | null;
}

function fmtTimestamp(d: Date): string {
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

interface BadgeState {
  text: string;
  className: string;
}

function badgeForState(state: "live" | "paused" | "closed"): BadgeState {
  if (state === "live") return { text: "LIVE", className: "live-preview__badge--live" };
  if (state === "paused") return { text: "PAUSED", className: "live-preview__badge--paused" };
  return { text: "CLOSED", className: "live-preview__badge--closed" };
}

function revokeObjectUrl(state: InternalState): void {
  if (state.objectUrl === null) return;
  URL.revokeObjectURL(state.objectUrl);
  state.objectUrl = null;
}

export function mountLivePreview(container: HTMLElement, opts: LivePreviewOptions): LivePreviewHandle {
  container.innerHTML = "";
  container.classList.add("live-preview");
  container.setAttribute("data-testid", "live-preview");

  const state: InternalState = {
    stream: null,
    fallbackTimer: null,
    generation: 0,
    expectedCloseGeneration: null,
    destroyed: false,
    closed: false,
    objectUrl: null,
  };

  // Closed-session placeholder: never opens a stream.
  if (!opts.isLive) {
    const note = document.createElement("p");
    note.className = "live-preview__placeholder";
    note.setAttribute("data-testid", "live-preview-placeholder");
    note.textContent = "session closed — live preview not available";
    container.append(note);

    const badge = document.createElement("span");
    const b = badgeForState("closed");
    badge.className = `live-preview__badge ${b.className}`;
    badge.setAttribute("data-testid", "live-preview-badge");
    badge.textContent = b.text;
    container.append(badge);

    return {
      start: () => {
        // no-op: closed sessions never stream
      },
      stop: () => {
        // no-op
      },
      setInterval: () => {
        // no-op: WebSocket cadence is controlled by the backend
      },
      markClosed: () => {
        // already closed
      },
      destroy: () => {
        state.destroyed = true;
        container.innerHTML = "";
        container.classList.remove("live-preview");
        log.info({ event: "live_preview_destroyed", session_id: opts.sessionId });
      },
    };
  }

  const toolbar = document.createElement("div");
  toolbar.className = "live-preview__toolbar";

  const playBtn = document.createElement("button");
  playBtn.type = "button";
  playBtn.className = "live-preview__play";
  playBtn.setAttribute("data-testid", "live-preview-play");
  playBtn.setAttribute("aria-label", "Resume live preview");
  playBtn.textContent = "▶";

  const fullscreenBtn = document.createElement("button");
  fullscreenBtn.type = "button";
  fullscreenBtn.className = "live-preview__play";
  fullscreenBtn.setAttribute("data-testid", "live-preview-fullscreen");
  fullscreenBtn.setAttribute("aria-label", "Toggle fullscreen live preview");
  fullscreenBtn.textContent = "⛶";

  const lastUpdate = document.createElement("span");
  lastUpdate.className = "live-preview__timestamp";
  lastUpdate.setAttribute("data-testid", "live-preview-timestamp");
  lastUpdate.textContent = "—";

  const badge = document.createElement("span");
  const pausedBadge = badgeForState("paused");
  badge.className = `live-preview__badge ${pausedBadge.className}`;
  badge.setAttribute("data-testid", "live-preview-badge");
  badge.textContent = pausedBadge.text;

  const errorIndicator = document.createElement("span");
  errorIndicator.className = "live-preview__error";
  errorIndicator.setAttribute("data-testid", "live-preview-error");
  errorIndicator.style.display = "none";

  toolbar.append(playBtn, fullscreenBtn, lastUpdate, errorIndicator, badge);

  const img = document.createElement("img");
  img.className = "live-preview__img";
  img.setAttribute("data-testid", "live-preview-img");
  img.setAttribute("alt", "Live browser preview");

  container.append(toolbar, img);

  const fullscreen = attachFullscreen(fullscreenBtn, container, opts.fullscreenMode ?? "native");

  const setBadge = (which: "live" | "paused" | "closed"): void => {
    const b = badgeForState(which);
    badge.className = `live-preview__badge ${b.className}`;
    badge.textContent = b.text;
  };

  const showStreamError = (): void => {
    errorIndicator.style.display = "";
    errorIndicator.textContent = "stream error; resume to reconnect";
  };

  const showFallbackNotice = (): void => {
    errorIndicator.style.display = "";
    errorIndicator.textContent = "screencast unavailable; using screenshot fallback";
  };

  const clearError = (): void => {
    errorIndicator.style.display = "none";
    errorIndicator.textContent = "";
  };

  const setPlayingUi = (): void => {
    setBadge("live");
    playBtn.textContent = "⏸";
    playBtn.setAttribute("aria-label", "Pause live preview");
  };

  const setPausedUi = (): void => {
    setBadge("paused");
    playBtn.textContent = "▶";
    playBtn.setAttribute("aria-label", "Resume live preview");
  };

  const closeStream = (expected: boolean): void => {
    if (state.stream === null) return;
    const stream = state.stream;
    const generation = state.generation;
    state.stream = null;
    if (expected) {
      state.expectedCloseGeneration = generation;
      state.generation += 1;
    }
    stream.close();
  };

  const stopFallbackPoll = (): void => {
    if (state.fallbackTimer === null) return;
    clearInterval(state.fallbackTimer);
    state.fallbackTimer = null;
  };

  const updateFallbackFrame = (): void => {
    if (state.destroyed || state.closed || state.fallbackTimer === null) return;
    img.src = liveScreenshotUrl(opts.sessionId, {
      format: opts.format ?? "png",
      cacheBust: Date.now(),
    });
    lastUpdate.textContent = fmtTimestamp(new Date());
    setPlayingUi();
    showFallbackNotice();
  };

  const startFallbackPoll = (): void => {
    if (state.destroyed || state.closed || state.fallbackTimer !== null) return;
    state.fallbackTimer = setInterval(updateFallbackFrame, opts.intervalMs ?? 3000);
    updateFallbackFrame();
  };

  const stopActivePreview = (expectedStreamClose: boolean): void => {
    closeStream(expectedStreamClose);
    stopFallbackPoll();
  };

  const startStream = (): void => {
    if (state.destroyed || state.closed || state.stream !== null || state.fallbackTimer !== null) return;
    state.generation += 1;
    const generation = state.generation;
    const url = screencastWsUrl(
      opts.sessionId,
      opts.fps === undefined ? {} : { fps: opts.fps },
    );
    state.stream = openScreencast(url, {
      onFrame: (blob) => {
        if (state.destroyed || state.closed || state.generation !== generation) return;
        const nextUrl = URL.createObjectURL(blob);
        revokeObjectUrl(state);
        state.objectUrl = nextUrl;
        img.src = nextUrl;
        lastUpdate.textContent = fmtTimestamp(new Date());
        clearError();
        setPlayingUi();
      },
      onError: () => {
        if (state.destroyed || state.closed || state.generation !== generation) return;
        showStreamError();
        log.warn({ event: "live_preview_stream_error", session_id: opts.sessionId });
      },
      onClose: (event) => {
        if (state.destroyed || state.closed || state.generation !== generation) return;
        if (state.expectedCloseGeneration === generation) {
          state.expectedCloseGeneration = null;
          return;
        }
        state.stream = null;
        state.generation += 1;
        startFallbackPoll();
        log.warn({
          event: "live_preview_stream_closed",
          session_id: opts.sessionId,
          code: event.code,
          reason: event.reason,
          was_clean: event.wasClean,
        });
      },
      ...(opts.webSocketCtor ? { webSocketCtor: opts.webSocketCtor } : {}),
    });
    clearError();
    setPlayingUi();
  };

  const handle: LivePreviewHandle = {
    start: () => {
      const wasRunning = state.stream !== null;
      startStream();
      if (!wasRunning && state.stream !== null) {
        log.info({
          event: "live_preview_started",
          session_id: opts.sessionId,
          fps: opts.fps,
        });
      }
    },
    stop: () => {
      if (state.destroyed || state.closed) return;
      const wasRunning = state.stream !== null || state.fallbackTimer !== null;
      stopActivePreview(true);
      setPausedUi();
      clearError();
      if (wasRunning) {
        log.info({ event: "live_preview_paused", session_id: opts.sessionId });
      }
    },
    setInterval: () => {
      // no-op: WebSocket cadence is controlled by the backend
    },
    markClosed: () => {
      if (state.destroyed || state.closed) return;
      state.closed = true;
      stopActivePreview(true);
      revokeObjectUrl(state);
      const b = badgeForState("closed");
      badge.className = `live-preview__badge ${b.className}`;
      badge.textContent = b.text;
      playBtn.disabled = true;
      playBtn.setAttribute("aria-label", "Session closed");
      fullscreenBtn.disabled = true;
      fullscreenBtn.setAttribute("aria-label", "Session closed");
      fullscreen.destroy();
      clearError();
      log.info({ event: "live_preview_marked_closed", session_id: opts.sessionId });
    },
    destroy: () => {
      if (state.destroyed) return;
      state.destroyed = true;
      stopActivePreview(true);
      revokeObjectUrl(state);
      fullscreen.destroy();
      container.innerHTML = "";
      container.classList.remove("live-preview");
      log.info({ event: "live_preview_destroyed", session_id: opts.sessionId });
    },
  };

  playBtn.addEventListener("click", () => {
    if (state.stream !== null || state.fallbackTimer !== null) {
      handle.stop();
    } else {
      handle.start();
      log.info({ event: "live_preview_resumed", session_id: opts.sessionId });
    }
  });

  return handle;
}
