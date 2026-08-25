// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * The terminal plugin's dashboard renderer.
 *
 * Ports `terminal-view.ts` and the feed half of `session-terminal.ts`
 * (core's pre-extraction terminal page) onto the published stream-renderer
 * contract (`packages/octowright-frontend/src/plugin-contract.d.ts`). Core
 * now owns everything `session-terminal.ts` used to do EXCEPT mounting
 * xterm and writing output deltas to it: header, footer, the auth notice,
 * the action timeline, the `/tail` WebSocket and the events fetch are
 * `bootStreamSession`'s job (`session-stream.ts`), not this module's -- see
 * that file's docstring, which spells out the eight things core does versus
 * the one thing (`mountStream`) a stream-layout plugin does.
 *
 * `SessionEvent`/`StreamContext`/`StreamHandle` below are a deliberate
 * structural COPY of the published contract, not an import of it -- the
 * contract's own docstring makes the same choice for the same reason (a
 * third party should not have to reach into core's source tree to build
 * against it), and TypeScript's structural typing means an accidental
 * divergence here still compiles, which is exactly what the vitest runtime
 * test in `packages/octowright-frontend/tests` exists to catch instead: it
 * imports the BUILT module and drives it through the real
 * mount/feed/destroy cycle against the real shapes, so a signature drift
 * fails at test time regardless of which side's copy moved.
 *
 * Bundled standalone (see `../build.mjs`) into one self-contained
 * `renderer.js` with xterm and its two addons inlined: there is no bundler
 * on the serving path (`http/routes/plugin_assets.py` serves this file
 * verbatim off disk), so a bare `import "@xterm/xterm"` surviving into the
 * output would 404 in the browser. xterm's own stylesheet is inlined the
 * same way -- imported through esbuild's `text` loader (see `css.d.ts`) as
 * a plain string and injected via a `<style>` tag on first mount, since
 * there is no separate-asset-loading step on the serving side that would
 * pull in a sibling `.css` file for a plugin module.
 */

import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";
import xtermCss from "@xterm/xterm/css/xterm.css";

/** One recorded JSONL row. Structural mirror of the published SessionEvent. */
interface SessionEvent {
  ts: string;
  action: string;
  [field: string]: unknown;
}

/** What core hands a renderer at mount time. Structural mirror of StreamContext. */
interface StreamContext {
  sessionId: string;
  live: boolean;
  kind: string;
}

/** Structural mirror of StreamHandle. */
interface StreamHandle {
  feed(events: SessionEvent[]): void;
  destroy(): void;
}

const STYLE_ELEMENT_ID = "octowright-terminal-plugin-style";

// The panel chrome core used to provide for the OLD dedicated terminal page
// (`.session-terminal-view` in `packages/octowright-frontend/static/styles.css`)
// does not exist for the generic stream layout core now serves every
// plugin through -- `.session-stream` carries zero styling of its own (see
// `session-stream.ts` / `plugin-registry.ts`), so a stream-layout plugin owns
// its own panel presentation. This mirrors that pre-extraction chrome,
// scoped under a plugin-private class name so it can't collide with core's.
const PANEL_CSS = `
.octowright-terminal-stream {
  background: #0b0b0e;
  border: 1px solid var(--border, #333);
  border-radius: 6px;
  padding: 6px;
  overflow: hidden;
  display: flex;
  justify-content: center;
  align-items: flex-start;
}
.octowright-terminal-stream__screen {
  /* No FitAddon: xterm renders at its fixed 80-col geometry -- terminal
     sessions (telnet/ssh) hardcode 80 cols and BBS content is authored for
     exactly that width. This div shrink-wraps to the canvas so the panel
     doesn't force a wider layout. */
  display: inline-block;
  min-height: 360px;
}
`;

function ensureStylesInjected(): void {
  if (document.getElementById(STYLE_ELEMENT_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ELEMENT_ID;
  style.textContent = `${xtermCss}\n${PANEL_CSS}`;
  document.head.appendChild(style);
}

function buildTerminal(): Terminal {
  // Fixed geometry, matching the pre-extraction view exactly (see
  // terminal-view.ts's `defaultFactory`): no FitAddon, because fitting to
  // the container would inflate cols past the 80-col width BBS/telnet
  // content is authored for, stretching ANSI art into an unreadable wide
  // layout.
  const term = new Terminal({
    convertEol: false,
    disableStdin: true,
    cursorBlink: false,
    scrollback: 5000,
    fontSize: 13,
    cols: 80,
    rows: 25,
    allowProposedApi: true,
  });
  term.loadAddon(new WebLinksAddon());
  const unicode = new Unicode11Addon();
  term.loadAddon(unicode);
  term.unicode.activeVersion = "11";
  return term;
}

/**
 * Mount a read-only xterm screen into `el` and return a handle that writes
 * each fed `terminal_output` delta verbatim.
 *
 * Synchronous rather than async: nothing here needs to await (xterm's
 * constructor and `open()` are both synchronous), and the contract allows
 * either (`StreamHandle | Promise<StreamHandle>`) -- core awaits either
 * shape before the first `feed`.
 */
export function mountStream(el: HTMLElement, ctx: StreamContext): StreamHandle {
  ensureStylesInjected();

  el.innerHTML = "";
  el.classList.add("octowright-terminal-stream");
  el.setAttribute("data-terminal-stream", ctx.sessionId);

  const screen = document.createElement("div");
  screen.className = "octowright-terminal-stream__screen";
  screen.setAttribute("data-testid", "terminal-screen");
  el.appendChild(screen);

  const term = buildTerminal();
  term.open(screen);

  return {
    feed(events: SessionEvent[]): void {
      for (const event of events) {
        if (event.action !== "terminal_output") continue;
        const data = event.data;
        if (typeof data !== "string") continue;
        // `reset: true` means the connector's buffer was cleared and `data`
        // is the full new buffer. Emit RIS (ESC c) IN the write stream
        // rather than calling `term.reset()` out of band: xterm's `write()`
        // is async-buffered, so an out-of-band reset would run before the
        // previously-queued delta is parsed, and that stale delta would
        // then reappear on the freshly-reset screen. An in-stream `\x1bc` is
        // parsed in order -- prior delta, then full reset, then this delta
        // -- which is why `terminal-view.ts` did it this way, and why this
        // keeps doing it that way rather than the simpler-looking
        // `term.reset()` call the task brief described.
        term.write(event.reset === true ? `\x1bc${data}` : data);
      }
    },
    destroy(): void {
      term.dispose();
      el.innerHTML = "";
      el.classList.remove("octowright-terminal-stream");
      el.removeAttribute("data-terminal-stream");
    },
  };
}
