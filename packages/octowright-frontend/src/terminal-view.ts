// Dashboard terminal screen — feeds recorded `terminal_output` deltas into an
// xterm.js instance. Read-only: it shows program OUTPUT only, never sends
// keystrokes and never echoes recorded input (see Phase 4 plan, "View
// semantics"). xterm does its own ANSI emulation, so each delta — which is the
// raw output stream with escapes preserved — is written verbatim.

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

export type TerminalFactory = () => TerminalLike;

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
  destroy(): void;
}

function defaultFactory(): TerminalLike {
  // 80x24 matches the PTY default; SSH fixes its own size. scrollback keeps
  // history visible. convertEol:false because the stream already carries CRLF.
  return new Terminal({
    convertEol: false,
    disableStdin: true,
    cursorBlink: false,
    scrollback: 5000,
    fontSize: 13,
    cols: 80,
    rows: 24,
  }) as unknown as TerminalLike;
}

export function mountTerminalView(container: HTMLElement, opts: TerminalViewOptions): TerminalViewHandle {
  container.innerHTML = "";
  container.classList.add("session-terminal-view");
  container.setAttribute("data-testid", "terminal-view");

  const screen = document.createElement("div");
  screen.className = "session-terminal-view__screen";
  screen.setAttribute("data-testid", "terminal-screen");
  container.append(screen);

  const term = (opts.terminalFactory ?? defaultFactory)();
  term.open(screen);
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
    destroy(): void {
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
