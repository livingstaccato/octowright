// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * The renderer core uses when a plugin's own cannot run.
 *
 * Every path here renders a VISIBLE reason. A blank pane with a console error
 * is the worst possible failure for something a third party built: the operator
 * sees nothing, the plugin author hears nothing, and the dashboard looks broken
 * rather than degraded.
 *
 * It is a real renderer, not an error box — it still shows the session's events,
 * so a kind whose renderer failed is degraded rather than useless.
 */

import type { SessionEvent, StreamContext, StreamHandle } from "./plugin-contract.js";
import { appendTimelineEvents, renderTimeline } from "./timeline.js";

export type FallbackCode = "no-frontend" | "version-mismatch" | "import-failed" | "mount-failed";

export interface FallbackReason {
  code: FallbackCode;
  detail: string;
}

const HEADLINE: Record<FallbackCode, string> = {
  "no-frontend": "This session kind ships no renderer — showing the generic timeline.",
  "version-mismatch": "This renderer targets a different dashboard version — showing the generic timeline.",
  "import-failed": "This kind's renderer failed to load — showing the generic timeline.",
  "mount-failed": "This kind's renderer failed to render — showing the generic timeline.",
};

export function mountFallbackStream(
  el: HTMLElement,
  ctx: StreamContext,
  reason: FallbackReason,
): StreamHandle {
  el.innerHTML = "";
  el.classList.add("session-stream--fallback");

  const notice = document.createElement("div");
  notice.className = "session-stream-fallback-notice";
  notice.setAttribute("data-testid", "stream-fallback-notice");
  notice.setAttribute("data-fallback-code", reason.code);
  // Name the kind: with several plugins enabled, "a renderer failed" does not
  // tell an operator which package to look at.
  notice.textContent = `${HEADLINE[reason.code]} (kind: ${ctx.kind}${reason.detail ? ` — ${reason.detail}` : ""})`;

  const timeline = document.createElement("div");
  timeline.className = "session-stream-fallback-timeline";
  timeline.setAttribute("data-testid", "stream-fallback-timeline");

  el.append(notice, timeline);

  let base: string | null = null;
  let destroyed = false;

  return {
    feed(events: SessionEvent[]): void {
      if (destroyed || events.length === 0) return;
      if (base === null) {
        // `events[0]` is guaranteed by the `events.length === 0` guard above --
        // the `?? new Date().toISOString()` fallback is not dead code, it is a
        // defence against a malformed event off the wire: TypeScript's `ts:
        // string` requirement is a compile-time guarantee only and does not
        // survive `JSON.parse`. This is the one path whose whole job is to not
        // crash, so a missing `ts` degrades the render rather than throwing.
        base = events[0]?.ts ?? new Date().toISOString();
        renderTimeline(timeline, events);
        return;
      }
      appendTimelineEvents(timeline, events, base);
    },
    destroy(): void {
      destroyed = true;
    },
  };
}
