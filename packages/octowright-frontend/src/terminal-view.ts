// Dashboard terminal screen — feeds recorded `terminal_output` deltas into an
// xterm.js instance. Read-only: it shows program OUTPUT only, never sends
// keystrokes and never echoes recorded input (see Phase 4 plan, "View
// semantics"). xterm does its own ANSI emulation, so each delta — which is the
// raw output stream with escapes preserved — is written verbatim.
//
// Addons: FitAddon (fit the DISPLAY to its container — read-only, so this never
// resizes the underlying PTY), WebLinksAddon (clickable URLs in output),
// Unicode11Addon (correct wide-char/emoji widths; needs allowProposedApi).
// This module + xterm + addons are loaded lazily (dynamic import from
// session.ts) so they never bloat the browser-session bundle.

import { FitAddon } from "@xterm/addon-fit";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { getLogger } from "./telemetry.js";
import type { RecordingEvent } from "./types.js";

const log = getLogger("octowright.frontend.terminal-view");

/** Minimal slice of xterm's Terminal that we depend on — lets tests inject a
 *  fake without a real renderer (jsdom has no layout). */
export interface TerminalLike {
  open(el: HTMLElement): void;
  write(data: string): void;
  reset(): void;
  dispose(): void;
}

/** A terminal plus its fit hook. The factory owns addon wiring so the view
 *  stays agnostic of how "fit to container" is implemented (real FitAddon vs a
 *  test no-op). */
export interface TerminalInstance {
  terminal: TerminalLike;
  /** Resize the DISPLAY to fit its container. No-op in tests. */
  fit: () => void;
}

export type TerminalFactory = () => TerminalInstance;

export interface TerminalViewOptions {
  sessionId: string;
  /** Inject a fake terminal in tests. Defaults to a real xterm Terminal. */
  terminalFactory?: TerminalFactory;
}

export interface TerminalViewHandle {
  /** Write a single output delta to the screen. */
  write(data: string): void;
  /** Clear the screen (e.g. before replaying from scratch). */
  reset(): void;
  /** Filter a batch of recording events; write each terminal_output delta in order. */
  feedEvents(events: RecordingEvent[]): void;
  /** Refit the display to its container (called on resize). */
  fit(): void;
  destroy(): void;
}

function defaultFactory(): TerminalInstance {
  // 80x24 is the initial size; FitAddon immediately reflows it to the
  // container. scrollback keeps history visible. convertEol:false because the
  // stream already carries CRLF. allowProposedApi is required by Unicode11Addon.
  const term = new Terminal({
    convertEol: false,
    disableStdin: true,
    cursorBlink: false,
    scrollback: 5000,
    fontSize: 13,
    cols: 80,
    rows: 24,
    allowProposedApi: true,
  });
  const fitAddon = new FitAddon();
  term.loadAddon(fitAddon);
  term.loadAddon(new WebLinksAddon());
  const unicode = new Unicode11Addon();
  term.loadAddon(unicode);
  term.unicode.activeVersion = "11";
  return { terminal: term as unknown as TerminalLike, fit: () => fitAddon.fit() };
}

export function mountTerminalView(container: HTMLElement, opts: TerminalViewOptions): TerminalViewHandle {
  container.innerHTML = "";
  container.classList.add("session-terminal-view");
  container.setAttribute("data-testid", "terminal-view");

  const screen = document.createElement("div");
  screen.className = "session-terminal-view__screen";
  screen.setAttribute("data-testid", "terminal-screen");
  container.append(screen);

  // Destructured as `fitToContainer` (not `fit`) so biome's noFocusedTests
  // rule doesn't mistake the bare `fit()` call for a Jest-style focused test.
  const { terminal: term, fit: fitToContainer } = (opts.terminalFactory ?? defaultFactory)();
  term.open(screen);

  const doFit = (): void => {
    try {
      fitToContainer();
    } catch (err) {
      log.debug({ event: "terminal_view_fit_failed", session_id: opts.sessionId, error: String(err) });
    }
  };
  doFit(); // initial fit to the container

  // Reflow the display when the container or window changes size. ResizeObserver
  // is the precise signal (panel resize, layout shifts); window resize is a
  // cheap backstop. jsdom has no ResizeObserver, so guard the construction.
  let ro: ResizeObserver | null = null;
  if (typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver(() => doFit());
    ro.observe(screen);
  }
  const onWindowResize = (): void => doFit();
  window.addEventListener("resize", onWindowResize);

  log.info({ event: "terminal_view_mounted", session_id: opts.sessionId });

  return {
    write(data: string): void {
      term.write(data);
    },
    reset(): void {
      term.reset();
    },
    feedEvents(events: RecordingEvent[]): void {
      for (const ev of events) {
        if (ev.action === "terminal_output" && typeof ev.data === "string") {
          term.write(ev.data);
        }
      }
    },
    fit: doFit,
    destroy(): void {
      window.removeEventListener("resize", onWindowResize);
      if (ro) ro.disconnect();
      try {
        term.dispose();
      } catch (err) {
        log.debug({ event: "terminal_view_dispose_failed", session_id: opts.sessionId, error: String(err) });
      }
      container.innerHTML = "";
      container.classList.remove("session-terminal-view");
      log.info({ event: "terminal_view_destroyed", session_id: opts.sessionId });
    },
  };
}
