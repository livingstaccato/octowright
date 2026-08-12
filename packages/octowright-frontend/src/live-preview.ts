// Live preview panel: streams live browser frames over a single WebSocket.

import { liveScreenshotUrl, screencastWsUrl } from "./api.js";
import { fetchDashboardMediaObjectUrl, getDashboardBearer } from "./dashboard-auth.js";
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
  /** Inject fetch for authenticated screenshot fallback requests. */
  mediaFetch?: typeof fetch;
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

/** Base cadence for the screenshot fallback when no interval was supplied. */
const FALLBACK_INTERVAL_MS = 3000;
/** Ceiling for the consecutive-failure backoff. */
const FALLBACK_MAX_INTERVAL_MS = 30000;
/** Failures past this stop lengthening the delay (2^4 × base, capped above). */
const FALLBACK_MAX_BACKOFF_STEPS = 4;

interface InternalState {
  stream: ScreencastHandle | null;
  /** Pending next-tick handle. Null while a request is in flight — use
   * `fallbackActive` to ask whether the fallback is running. */
  fallbackTimer: ReturnType<typeof setTimeout> | null;
  fallbackActive: boolean;
  /** Set while an <img> fetch is outstanding so a slow screenshot endpoint
   * can't have ticks stack requests and abort each other's images. */
  fallbackInflight: boolean;
  fallbackErrors: number;
  /** Detaches the in-flight tick's load/error listeners. */
  fallbackCleanup: (() => void) | null;
  fallbackAbort: AbortController | null;
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
    fallbackActive: false,
    fallbackInflight: false,
    fallbackErrors: 0,
    fallbackCleanup: null,
    fallbackAbort: null,
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
  errorIndicator.setAttribute("role", "status");
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
    if (state.fallbackTimer !== null) {
      clearTimeout(state.fallbackTimer);
      state.fallbackTimer = null;
    }
    state.fallbackAbort?.abort();
    state.fallbackCleanup?.();
    state.fallbackAbort = null;
    state.fallbackActive = false;
    state.fallbackInflight = false;
    state.fallbackErrors = 0;
  };

  const fallbackBaseIntervalMs = (): number => opts.intervalMs ?? FALLBACK_INTERVAL_MS;

  /** Base interval, doubled per consecutive failure, capped. */
  const fallbackNextDelayMs = (): number => {
    const steps = Math.min(state.fallbackErrors, FALLBACK_MAX_BACKOFF_STEPS);
    return Math.min(FALLBACK_MAX_INTERVAL_MS, fallbackBaseIntervalMs() * 2 ** steps);
  };

  const scheduleFallbackTick = (delayMs: number): void => {
    if (!state.fallbackActive || state.destroyed || state.closed) return;
    if (state.fallbackTimer !== null) clearTimeout(state.fallbackTimer);
    state.fallbackTimer = setTimeout(fallbackTick, delayMs);
  };

  function fallbackTick(): void {
    state.fallbackTimer = null;
    if (!state.fallbackActive || state.destroyed || state.closed) return;
    // A screenshot slower than the poll interval must not be replaced mid-flight:
    // swapping img.src aborts the pending load, so a slow server would abort
    // every frame forever while the endpoint keeps doing the work.
    if (state.fallbackInflight) {
      scheduleFallbackTick(fallbackBaseIntervalMs());
      return;
    }

    const cleanup = (): void => {
      state.fallbackInflight = false;
      state.fallbackCleanup = null;
      state.fallbackAbort = null;
      img.removeEventListener("load", onLoad);
      img.removeEventListener("error", onError);
    };
    const onLoad = (): void => {
      cleanup();
      state.fallbackErrors = 0;
      lastUpdate.textContent = fmtTimestamp(new Date());
      scheduleFallbackTick(fallbackBaseIntervalMs());
    };
    const onError = (): void => {
      cleanup();
      state.fallbackErrors += 1;
      log.warn({
        event: "live_preview_fallback_error",
        session_id: opts.sessionId,
        consecutive_errors: state.fallbackErrors,
      });
      scheduleFallbackTick(fallbackNextDelayMs());
    };

    state.fallbackInflight = true;
    state.fallbackCleanup = cleanup;
    img.addEventListener("load", onLoad);
    img.addEventListener("error", onError);
    const screenshotUrl = liveScreenshotUrl(opts.sessionId, {
      format: opts.format ?? "png",
      cacheBust: Date.now(),
    });
    if (getDashboardBearer() === null) {
      img.src = screenshotUrl;
      return;
    }

    const controller = new AbortController();
    state.fallbackAbort = controller;
    void fetchDashboardMediaObjectUrl(screenshotUrl, {
      signal: controller.signal,
      ...(opts.mediaFetch ? { fetchFn: opts.mediaFetch } : {}),
    })
      .then((nextUrl) => {
        if (!state.fallbackActive || state.destroyed || state.closed || controller.signal.aborted) {
          URL.revokeObjectURL(nextUrl);
          cleanup();
          return;
        }
        revokeObjectUrl(state);
        state.objectUrl = nextUrl;
        img.src = nextUrl;
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        cleanup();
        state.fallbackErrors += 1;
        errorIndicator.style.display = "";
        errorIndicator.textContent = "screenshot fallback unavailable; keeping last frame";
        log.warn({
          event: "live_preview_fallback_fetch_error",
          session_id: opts.sessionId,
          consecutive_errors: state.fallbackErrors,
          error: String(error),
        });
        scheduleFallbackTick(fallbackNextDelayMs());
      });
  }

  const startFallbackPoll = (): void => {
    if (state.destroyed || state.closed || state.fallbackActive) return;
    state.fallbackActive = true;
    state.fallbackErrors = 0;
    setPlayingUi();
    showFallbackNotice();
    fallbackTick();
  };

  const stopActivePreview = (expectedStreamClose: boolean): void => {
    closeStream(expectedStreamClose);
    stopFallbackPoll();
  };

  const startStream = (): void => {
    if (state.destroyed || state.closed || state.stream !== null || state.fallbackActive) return;
    state.generation += 1;
    const generation = state.generation;
    const url = screencastWsUrl(opts.sessionId, opts.fps === undefined ? {} : { fps: opts.fps });
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
      const wasRunning = state.stream !== null || state.fallbackActive;
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
    if (state.stream !== null || state.fallbackActive) {
      handle.stop();
    } else {
      handle.start();
      log.info({ event: "live_preview_resumed", session_id: opts.sessionId });
    }
  });

  return handle;
}
