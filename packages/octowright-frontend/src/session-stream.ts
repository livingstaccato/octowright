// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * The core-owned session detail page for a session-kind plugin's stream
 * renderer.
 *
 * `bootTerminalSession` (session-terminal.ts) builds this same header/slot/
 * timeline/footer layout and calls back into core for eight things --
 * renderHeader, renderFooter, installDashboardAuthRequiredNotice,
 * renderTimeline, appendTimelineEvents, openTail, getEvents,
 * tailWebSocketUrl -- with only mounting the view and feeding it events
 * actually terminal-specific. This is the generic version: core does all
 * eight, the plugin does exactly one (`mountStream`).
 *
 * `session-terminal.ts` is NOT modified by this module. Terminal moves onto
 * this path in the extraction step, when it becomes an external plugin;
 * until then core deliberately carries both.
 */

import { getEvents, tailWebSocketUrl } from "./api.js";
import type { MountStream, StreamContext, StreamHandle } from "./plugin-contract.js";
import { mountFallbackStream, type FallbackReason } from "./session-fallback.js";
import { installDashboardAuthRequiredNotice, renderFooter, renderHeader } from "./session.js";
import { openTail } from "./tail.js";
import { getLogger } from "./telemetry.js";
import { appendTimelineEvents, renderTimeline } from "./timeline.js";
import type { RecordingEvent, SessionDetail } from "./types.js";

const log = getLogger("octowright.frontend.session-stream");

export interface StreamPageRefs {
  header: HTMLElement;
  streamSlot: HTMLElement;
  timeline: HTMLElement;
  footer: HTMLElement;
}

export function buildStreamLayout(root: HTMLElement): StreamPageRefs {
  root.innerHTML = "";
  root.classList.add("session-page", "session-page--stream");

  const header = document.createElement("header");
  header.className = "session-header";
  header.setAttribute("data-testid", "session-header");

  const streamSlot = document.createElement("section");
  streamSlot.className = "session-stream";
  streamSlot.setAttribute("data-testid", "session-stream");

  const timeline = document.createElement("div");
  timeline.className = "session-timeline";
  timeline.setAttribute("data-testid", "session-timeline");

  const footer = document.createElement("footer");
  footer.className = "session-footer";
  footer.setAttribute("data-testid", "session-footer");

  root.append(header, streamSlot, timeline, footer);
  return { header, streamSlot, timeline, footer };
}

export interface StreamBootOptions {
  webSocketCtor?: typeof WebSocket;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * Wrap the dynamic import of a plugin's renderer module so a 404 or a
 * syntax error in a third party's module becomes a visible `FallbackReason`
 * rather than an unhandled rejection.
 *
 * Deliberately does not validate the module's shape beyond the import
 * itself succeeding: a module that resolves but has no `mountStream` export
 * surfaces at the mount call site in `bootStreamSession` (calling
 * `undefined` throws there), which already produces the same
 * `code: "mount-failed"` fallback -- no separate check is needed here.
 */
export async function importRenderer(
  moduleUrl: string,
): Promise<{ mountStream: MountStream } | FallbackReason> {
  try {
    const mod = (await import(/* @vite-ignore */ moduleUrl)) as { mountStream: MountStream };
    return { mountStream: mod.mountStream };
  } catch (err) {
    const detail = errorMessage(err);
    log.warn({ event: "plugin_import_failed", module_url: moduleUrl, error: detail });
    return { code: "import-failed", detail };
  }
}

/**
 * Build the fallback renderer for a mount/feed failure and log why -- the
 * on-page notice is enough for the operator, but the person debugging their
 * own plugin renderer needs it in the console too.
 */
function fallbackFromError(
  el: HTMLElement,
  ctx: StreamContext,
  err: unknown,
  event: string,
): StreamHandle {
  const detail = errorMessage(err);
  log.warn({ event, session_id: ctx.sessionId, kind: ctx.kind, error: detail });
  return mountFallbackStream(el, ctx, { code: "mount-failed", detail });
}

export async function bootStreamSession(
  root: HTMLElement,
  sessionId: string,
  detail: SessionDetail,
  mount: MountStream,
  opts: StreamBootOptions = {},
): Promise<void> {
  log.info({ event: "stream_boot_start", session_id: sessionId, kind: detail.kind, live: detail.live });
  const refs = buildStreamLayout(root);
  const removeAuthRequiredNotice = installDashboardAuthRequiredNotice(root);
  window.addEventListener("beforeunload", removeAuthRequiredNotice, { once: true });
  renderHeader(refs.header, detail);
  renderFooter(refs.footer, detail);

  const ctx: StreamContext = { sessionId, live: detail.live, kind: detail.kind };

  // `mount` is awaited before anything is fed to the handle it returns --
  // it may be async, and core must not feed a handle that isn't ready yet.
  let handle: StreamHandle;
  try {
    handle = await mount(refs.streamSlot, ctx);
  } catch (err) {
    handle = fallbackFromError(refs.streamSlot, ctx, err, "stream_mount_failed");
  }
  window.addEventListener("beforeunload", () => handle.destroy());

  // Every feed is wrapped the same way: a throwing plugin switches the pane
  // to the fallback rather than breaking the page, and the batch that broke
  // it is re-fed to the fallback so nothing recorded is lost from view.
  const feed = (events: RecordingEvent[]): void => {
    try {
      handle.feed(events);
    } catch (err) {
      // The plugin's own handle is being discarded -- release whatever it
      // holds (a socket, a timer, an observer) before losing the only
      // reference to it. `beforeunload` above closes over `handle` and will
      // only ever destroy whatever it currently points to, so if we don't
      // destroy the outgoing handle here nothing ever will. This is itself
      // failure-handling code, so a throwing `destroy()` must not stop the
      // fallback swap that follows it.
      try {
        handle.destroy();
      } catch (destroyErr) {
        log.warn({
          event: "stream_handle_destroy_failed",
          session_id: ctx.sessionId,
          kind: ctx.kind,
          error: errorMessage(destroyErr),
        });
      }
      handle = fallbackFromError(refs.streamSlot, ctx, err, "stream_feed_failed");
      handle.feed(events);
    }
  };

  // Replay the full recorded history into the plugin, then render the
  // timeline. History is always fed before any live event.
  const initial = await getEvents(sessionId, 0);
  let baseIso = initial.events[0]?.ts ?? new Date().toISOString();
  renderTimeline(refs.timeline, initial.events);
  feed(initial.events);

  if (detail.live) {
    // Start the tail AFTER the history cursor so the first WS frame doesn't
    // replay deltas we already fed.
    const tail = openTail(tailWebSocketUrl(sessionId, initial.cursor), {
      onMessage: (msg) => {
        if (msg.events.length > 0) {
          if (initial.events.length === 0) {
            const first = msg.events[0];
            if (first) baseIso = first.ts;
          }
          appendTimelineEvents(refs.timeline, msg.events, baseIso);
          feed(msg.events);
        }
      },
      ...(opts.webSocketCtor ? { webSocketCtor: opts.webSocketCtor } : {}),
    });
    window.addEventListener("beforeunload", () => tail.close());
  }
  log.info({ event: "stream_boot_complete", session_id: sessionId, kind: detail.kind });
}
