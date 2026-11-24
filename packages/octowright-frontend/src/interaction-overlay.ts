import { getInteractions } from "./api.js";
import { DASHBOARD_AUTH_REQUIRED_EVENT } from "./dashboard-auth.js";
import { fittedCanvas, InteractionPlayback, mapInteractionPoint, supportedGeometry } from "./interaction-playback.js";
import type { InteractionSidecarRow } from "./types.js";

interface OverlayOptions {
  sessionId: string;
  video: HTMLVideoElement;
  videoBasename: string | null;
  fetchPage?: typeof getInteractions;
  requestFrame?: typeof requestAnimationFrame;
  cancelFrame?: typeof cancelAnimationFrame;
}
export interface InteractionOverlayHandle {
  destroy(): void;
  setVideo(basename: string | null): void;
}

/** Dashboard-only decoration; native Range media/auth ownership stays with the player. */
export function mountInteractionOverlay(container: HTMLElement, options: OverlayOptions): InteractionOverlayHandle {
  const { video } = options;
  const stage = document.createElement("div");
  stage.className = "interaction-stage";
  container.insertBefore(stage, video);
  stage.append(video);
  const layer = document.createElement("div");
  layer.className = "interaction-layer";
  layer.setAttribute("aria-hidden", "true");
  stage.append(layer);
  const controls = document.createElement("div");
  controls.className = "interaction-controls";
  const label = document.createElement("label"),
    checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.disabled = true;
  label.append(checkbox, document.createTextNode(" Show interactions"));
  const status = document.createElement("p");
  status.setAttribute("role", "status");
  status.textContent = "Loading interaction metadata…";
  const note = document.createElement("p");
  note.textContent =
    "Timing is estimated from each page's video origin. Live indicators recorded in video pixels remain visible when this control is off.";
  controls.append(label, status, note);
  stage.after(controls);
  const abort = new AbortController();
  const requestFrame = options.requestFrame ?? requestAnimationFrame;
  const cancelFrame = options.cancelFrame ?? cancelAnimationFrame;
  let frame: number | null = null,
    disposed = false,
    generation = 0;
  let model: InteractionPlayback | null = null,
    selected = options.videoBasename;
  let truncated = false,
    incomplete = false;
  const stop = (): void => {
    if (frame !== null) cancelFrame(frame);
    frame = null;
  };
  const message = (extra = ""): void => {
    const text = [
      truncated ? "Interaction capture truncated; some input is missing." : "",
      incomplete ? "Interaction metadata incomplete; loading stopped at an invalid or unsupported byte cursor." : "",
      model?.hasEvents ? "" : "No interactions recorded for this video.",
      extra,
    ]
      .filter(Boolean)
      .join(" ");
    const next = text || "Interaction metadata ready.";
    if (status.textContent !== next) status.textContent = next;
  };
  const draw = (): void => {
    layer.replaceChildren();
    if (disposed || !model || !checkbox.checked) return;
    const rect = video.getBoundingClientRect(),
      parent = stage.getBoundingClientRect();
    const canvas = fittedCanvas(video.videoWidth, video.videoHeight, {
      left: rect.left - parent.left,
      top: rect.top - parent.top,
      width: rect.width,
      height: rect.height,
    });
    if (!canvas) {
      message("Waiting for video dimensions.");
      return;
    }
    Object.assign(layer.style, {
      left: `${canvas.left}px`,
      top: `${canvas.top}px`,
      width: `${canvas.width}px`,
      height: `${canvas.height}px`,
    });
    let unsupported = false;
    for (const marker of model.at(video.currentTime * 1000)) {
      if (!supportedGeometry(marker.row, marker.geometry)) {
        unsupported = true;
        continue;
      }
      const point = mapInteractionPoint(marker.row, marker.geometry, video.videoWidth, video.videoHeight, {
        left: 0,
        top: 0,
        width: canvas.width,
        height: canvas.height,
      });
      if (!point) continue;
      const node = document.createElement("span");
      node.className = "interaction-marker";
      node.dataset.phase = marker.phase;
      node.dataset.pointer = marker.row.pointer_type;
      node.dataset.button = String(marker.row.button);
      node.textContent = marker.label;
      Object.assign(node.style, {
        left: `${point.x}px`,
        top: `${point.y}px`,
        opacity: String(marker.opacity),
        transform: `translate(-50%, -50%) scale(${1 + (1 - marker.opacity) * 0.6})`,
      });
      node.style.setProperty("--hold-progress", String(marker.progress));
      layer.append(node);
    }
    message(
      unsupported
        ? "Overlay geometry unavailable for this segment (missing geometry or Firefox viewport resize). Any live indicators already recorded remain visible."
        : model.hasBakedMarkers
          ? "Live indicators are already in the video; duplicate markers are suppressed."
          : "",
    );
  };
  const schedule = (): void => {
    if (
      !disposed &&
      model &&
      checkbox.checked &&
      !checkbox.disabled &&
      !video.paused &&
      !video.ended &&
      frame === null
    ) {
      frame = requestFrame(() => {
        frame = null;
        draw();
        schedule();
      });
    }
  };
  const update = (): void => {
    stop();
    draw();
    schedule();
  };
  const events = [
    "play",
    "pause",
    "ended",
    "timeupdate",
    "seeking",
    "seeked",
    "ratechange",
    "loadedmetadata",
    "resize",
    "emptied",
  ];
  for (const event of events) video.addEventListener(event, update);
  checkbox.addEventListener("change", update);
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
  observer?.observe(stage);
  const destroy = (): void => {
    if (disposed) return;
    disposed = true;
    generation++;
    abort.abort();
    stop();
    observer?.disconnect();
    for (const event of events) video.removeEventListener(event, update);
    checkbox.removeEventListener("change", update);
    window.removeEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, destroy);
    layer.remove();
    controls.remove();
    if (stage.parentNode) {
      stage.before(video);
      stage.remove();
    }
    model = null;
  };
  window.addEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, destroy);
  const load = async (): Promise<void> => {
    const current = ++generation,
      rows: InteractionSidecarRow[] = [];
    let cursor = 0,
      available = false;
    try {
      for (;;) {
        const page = await (options.fetchPage ?? getInteractions)(options.sessionId, cursor, 1000, abort.signal);
        if (disposed || current !== generation) return;
        available ||= page.available;
        truncated ||= page.truncated;
        // Cursor is a byte offset into the bounded sidecar, never an arbitrary URL.
        if (
          !Number.isFinite(page.next_cursor) ||
          !Number.isInteger(page.next_cursor) ||
          page.next_cursor < cursor ||
          page.next_cursor > 64 * 1024 * 1024
        ) {
          incomplete = true;
          truncated ||= page.next_cursor > 64 * 1024 * 1024;
          break;
        }
        rows.push(...page.events);
        truncated ||= page.events.some((row) => row.type === "truncated");
        if (page.complete || !page.available) break;
        if (page.next_cursor <= cursor) {
          incomplete = true;
          break;
        }
        cursor = page.next_cursor;
      }
      if (!available) {
        status.textContent =
          "Interaction metadata unavailable for this recording. Older recordings may not include it.";
        return;
      }
      model = new InteractionPlayback(rows, selected);
      checkbox.disabled = !model.hasEvents;
      message();
      update();
    } catch {
      if (!disposed && !abort.signal.aborted && current === generation)
        status.textContent = "Could not load interaction metadata. Reload the dashboard to retry.";
    }
  };
  void load();
  return {
    destroy,
    setVideo(basename) {
      if (disposed) return;
      selected = basename;
      model?.setVideo(basename);
      checkbox.disabled = !model?.hasEvents;
      message();
      update();
    },
  };
}
