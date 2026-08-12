// Terminal session detail page. A self-contained boot path for kind ===
// "terminal", kept separate from the browser bootSession so the browser path
// stays untouched. Slim layout: header + xterm screen + action timeline.

import { getEvents, tailWebSocketUrl } from "./api.js";
import { installDashboardAuthRequiredNotice, renderFooter, renderHeader } from "./session.js";
import { openTail } from "./tail.js";
import { getLogger } from "./telemetry.js";
import { mountTerminalView, type TerminalFactory } from "./terminal-view.js";
import { appendTimelineEvents, renderTimeline } from "./timeline.js";
import type { SessionDetail } from "./types.js";

const log = getLogger("octowright.frontend.session-terminal");

export interface TerminalPageRefs {
  header: HTMLElement;
  terminalSlot: HTMLElement;
  timeline: HTMLElement;
  footer: HTMLElement;
}

export function buildTerminalLayout(root: HTMLElement): TerminalPageRefs {
  root.innerHTML = "";
  root.classList.add("session-page", "session-page--terminal");

  const header = document.createElement("header");
  header.className = "session-header";
  header.setAttribute("data-testid", "session-header");

  const terminalSlot = document.createElement("section");
  terminalSlot.className = "session-terminal";
  terminalSlot.setAttribute("data-testid", "session-terminal");

  const timeline = document.createElement("div");
  timeline.className = "session-timeline";
  timeline.setAttribute("data-testid", "session-timeline");

  const footer = document.createElement("footer");
  footer.className = "session-footer";
  footer.setAttribute("data-testid", "session-footer");

  root.append(header, terminalSlot, timeline, footer);
  return { header, terminalSlot, timeline, footer };
}

export interface TerminalBootOptions {
  webSocketCtor?: typeof WebSocket;
  terminalFactory?: TerminalFactory;
}

export async function bootTerminalSession(
  root: HTMLElement,
  sessionId: string,
  detail: SessionDetail,
  opts: TerminalBootOptions = {},
): Promise<void> {
  log.info({ event: "terminal_boot_start", session_id: sessionId, live: detail.live });
  const refs = buildTerminalLayout(root);
  const removeAuthRequiredNotice = installDashboardAuthRequiredNotice(root);
  window.addEventListener("beforeunload", removeAuthRequiredNotice, { once: true });
  renderHeader(refs.header, detail);
  renderFooter(refs.footer, detail);

  const view = mountTerminalView(refs.terminalSlot, {
    sessionId,
    ...(opts.terminalFactory ? { terminalFactory: opts.terminalFactory } : {}),
  });
  window.addEventListener("beforeunload", () => view.destroy());

  // Replay the full recorded history into the xterm, then render the timeline.
  const initial = await getEvents(sessionId, 0);
  let baseIso = initial.events[0]?.ts ?? new Date().toISOString();
  renderTimeline(refs.timeline, initial.events);
  view.feedEvents(initial.events);

  if (detail.live) {
    // Start the tail AFTER the history cursor so the first WS frame doesn't
    // replay deltas we already wrote.
    const tail = openTail(tailWebSocketUrl(sessionId, initial.cursor), {
      onMessage: (msg) => {
        if (msg.events.length > 0) {
          if (initial.events.length === 0) {
            const first = msg.events[0];
            if (first) baseIso = first.ts;
          }
          appendTimelineEvents(refs.timeline, msg.events, baseIso);
          view.feedEvents(msg.events);
        }
      },
      ...(opts.webSocketCtor ? { webSocketCtor: opts.webSocketCtor } : {}),
    });
    window.addEventListener("beforeunload", () => tail.close());
  }
  log.info({ event: "terminal_boot_complete", session_id: sessionId });
}
