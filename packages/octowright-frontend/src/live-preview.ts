// Live preview panel — polls /api/sessions/{id}/screenshot/now on a tick.
//
// Engine-agnostic alternative to CDP screencast: a periodic <img>.src refresh
// against a live BrowserSession.page. 2-5s lag is fine for monitoring "what
// is my login flow stuck on right now" — the use case.

import { counter, histogram } from "@provide-io/telemetry";
import { liveScreenshotUrl } from "./api.js";
import { getLogger } from "./telemetry.js";

const log = getLogger("octowright.frontend.live-preview");

const ticksCounter = counter("octowright_frontend_live_preview_ticks_total", {
  description: "Live preview poll ticks",
});

const tickLatencyHistogram = histogram("octowright_frontend_live_preview_tick_ms", {
  description: "Live preview poll latency",
  unit: "ms",
});

export interface LivePreviewOptions {
  sessionId: string;
  isLive: boolean;
  /** Default 3000ms. */
  intervalMs?: number;
  /** Default 'jpeg' (smaller bytes for repeated polls). */
  format?: "png" | "jpeg";
}

export interface LivePreviewHandle {
  start: () => void;
  stop: () => void;
  setInterval: (ms: number) => void;
  /** Transition a previously-live preview to its closed state. Stops polling
   * and swaps the badge so the user sees "session closed" instead of a
   * stream of "transient error (0)" indicators. Idempotent. */
  markClosed: () => void;
  destroy: () => void;
}

const RATE_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 1000, label: "1s" },
  { value: 3000, label: "3s" },
  { value: 10000, label: "10s" },
];

const DEFAULT_INTERVAL_MS = 3000;
const MAX_BACKOFF_INTERVAL_MS = 10000;

interface InternalState {
  intervalMs: number;
  effectiveIntervalMs: number;
  consecutiveErrors: number;
  format: "png" | "jpeg";
  timer: ReturnType<typeof setInterval> | null;
  destroyed: boolean;
}

function nowMs(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
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

export function mountLivePreview(container: HTMLElement, opts: LivePreviewOptions): LivePreviewHandle {
  container.innerHTML = "";
  container.classList.add("live-preview");
  container.setAttribute("data-testid", "live-preview");

  const state: InternalState = {
    intervalMs: opts.intervalMs ?? DEFAULT_INTERVAL_MS,
    effectiveIntervalMs: opts.intervalMs ?? DEFAULT_INTERVAL_MS,
    consecutiveErrors: 0,
    format: opts.format ?? "jpeg",
    timer: null,
    destroyed: false,
  };

  // Closed-session placeholder: never polls.
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
        // no-op — closed sessions never poll
      },
      stop: () => {
        // no-op
      },
      setInterval: () => {
        // no-op
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

  // ---- Live session: full UI -------------------------------------------------
  const toolbar = document.createElement("div");
  toolbar.className = "live-preview__toolbar";

  const playBtn = document.createElement("button");
  playBtn.type = "button";
  playBtn.className = "live-preview__play";
  playBtn.setAttribute("data-testid", "live-preview-play");
  playBtn.setAttribute("aria-label", "Pause live preview");
  playBtn.textContent = "⏸"; // pause glyph; we start in playing state

  const rateSelect = document.createElement("select");
  rateSelect.className = "live-preview__rate";
  rateSelect.setAttribute("data-testid", "live-preview-rate");
  rateSelect.setAttribute("aria-label", "Live preview refresh rate");
  for (const opt of RATE_OPTIONS) {
    const o = document.createElement("option");
    o.value = String(opt.value);
    o.textContent = opt.label;
    rateSelect.append(o);
  }
  rateSelect.value = String(state.intervalMs);

  const lastUpdate = document.createElement("span");
  lastUpdate.className = "live-preview__timestamp";
  lastUpdate.setAttribute("data-testid", "live-preview-timestamp");
  lastUpdate.textContent = "—";

  const badge = document.createElement("span");
  const liveBadge = badgeForState("live");
  badge.className = `live-preview__badge ${liveBadge.className}`;
  badge.setAttribute("data-testid", "live-preview-badge");
  badge.textContent = liveBadge.text;

  const errorIndicator = document.createElement("span");
  errorIndicator.className = "live-preview__error";
  errorIndicator.setAttribute("data-testid", "live-preview-error");
  errorIndicator.style.display = "none";

  toolbar.append(playBtn, rateSelect, lastUpdate, errorIndicator, badge);

  const img = document.createElement("img");
  img.className = "live-preview__img";
  img.setAttribute("data-testid", "live-preview-img");
  img.setAttribute("alt", "Live browser preview");

  container.append(toolbar, img);

  const setBadge = (which: "live" | "paused"): void => {
    const b = badgeForState(which);
    badge.className = `live-preview__badge ${b.className}`;
    badge.textContent = b.text;
  };

  const showError = (status: number): void => {
    errorIndicator.style.display = "";
    errorIndicator.textContent = `transient error (${status})`;
  };

  const clearError = (): void => {
    errorIndicator.style.display = "none";
    errorIndicator.textContent = "";
  };

  const tick = (): void => {
    if (state.destroyed) return;
    const start = nowMs();
    const url = liveScreenshotUrl(opts.sessionId, {
      format: state.format,
      cacheBust: Date.now(),
    });
    // Drive the fetch via the <img> rather than fetch() so the browser handles
    // caching/decoding. We use load/error events to update the UI.
    const onLoad = (): void => {
      const latency = nowMs() - start;
      ticksCounter.add(1, { session_id: opts.sessionId });
      tickLatencyHistogram.record(latency, { session_id: opts.sessionId });
      lastUpdate.textContent = fmtTimestamp(new Date());
      clearError();
      state.consecutiveErrors = 0;
      if (state.effectiveIntervalMs !== state.intervalMs) {
        state.effectiveIntervalMs = state.intervalMs;
        if (state.timer !== null) {
          clearInterval(state.timer);
          state.timer = setInterval(tick, state.effectiveIntervalMs);
        }
      }
      log.debug({
        event: "live_preview_tick",
        session_id: opts.sessionId,
        latency_ms: latency,
      });
      img.removeEventListener("load", onLoad);
      img.removeEventListener("error", onError);
    };
    const onError = (): void => {
      state.consecutiveErrors += 1;
      state.effectiveIntervalMs = Math.min(
        MAX_BACKOFF_INTERVAL_MS,
        state.intervalMs * Math.pow(2, state.consecutiveErrors),
      );
      if (state.timer !== null) {
        clearInterval(state.timer);
        state.timer = setInterval(tick, state.effectiveIntervalMs);
      }
      log.warn({
        event: "live_preview_error",
        session_id: opts.sessionId,
        status: 0,
      });
      showError(0);
      img.removeEventListener("load", onLoad);
      img.removeEventListener("error", onError);
    };
    img.addEventListener("load", onLoad);
    img.addEventListener("error", onError);
    img.src = url;
  };

  const startPolling = (): void => {
    if (state.timer !== null) return;
    state.timer = setInterval(tick, state.effectiveIntervalMs);
    setBadge("live");
    playBtn.textContent = "⏸";
    playBtn.setAttribute("aria-label", "Pause live preview");
    // Fire one tick immediately so the user sees something within a frame.
    tick();
  };

  const stopPolling = (): void => {
    if (state.timer !== null) {
      clearInterval(state.timer);
      state.timer = null;
    }
    setBadge("paused");
    playBtn.textContent = "▶";
    playBtn.setAttribute("aria-label", "Resume live preview");
  };

  const handle: LivePreviewHandle = {
    start: () => {
      if (state.destroyed) return;
      const wasRunning = state.timer !== null;
      startPolling();
      if (!wasRunning) {
        log.info({
          event: "live_preview_started",
          session_id: opts.sessionId,
          interval_ms: state.intervalMs,
        });
      }
    },
    stop: () => {
      if (state.destroyed) return;
      const wasRunning = state.timer !== null;
      stopPolling();
      if (wasRunning) {
        log.info({ event: "live_preview_paused", session_id: opts.sessionId });
      }
    },
    setInterval: (ms: number) => {
      if (state.destroyed) return;
      state.intervalMs = ms;
      state.effectiveIntervalMs = ms;
      state.consecutiveErrors = 0;
      rateSelect.value = String(ms);
      log.info({
        event: "live_preview_interval_changed",
        session_id: opts.sessionId,
        new_ms: ms,
      });
      if (state.timer !== null) {
        clearInterval(state.timer);
        state.timer = setInterval(tick, state.effectiveIntervalMs);
      }
    },
    markClosed: () => {
      if (state.destroyed) return;
      stopPolling();
      // Swap the live toolbar's "PAUSED" badge for the closed-session one and
      // disable the play button so the user can't restart polling against a
      // dead page. The error indicator is cleared too — it would otherwise
      // sit on screen showing the last "transient error (0)".
      const b = badgeForState("closed");
      badge.className = `live-preview__badge ${b.className}`;
      badge.textContent = b.text;
      playBtn.disabled = true;
      playBtn.setAttribute("aria-label", "Session closed");
      clearError();
      log.info({ event: "live_preview_marked_closed", session_id: opts.sessionId });
    },
    destroy: () => {
      if (state.destroyed) return;
      state.destroyed = true;
      if (state.timer !== null) {
        clearInterval(state.timer);
        state.timer = null;
      }
      container.innerHTML = "";
      container.classList.remove("live-preview");
      log.info({ event: "live_preview_destroyed", session_id: opts.sessionId });
    },
  };

  // Wire toolbar interactions.
  playBtn.addEventListener("click", () => {
    if (state.timer !== null) {
      handle.stop();
    } else {
      handle.start();
      log.info({ event: "live_preview_resumed", session_id: opts.sessionId });
    }
  });
  rateSelect.addEventListener("change", () => {
    const next = Number.parseInt(rateSelect.value, 10);
    if (Number.isFinite(next) && next > 0) {
      handle.setInterval(next);
    }
  });

  return handle;
}
