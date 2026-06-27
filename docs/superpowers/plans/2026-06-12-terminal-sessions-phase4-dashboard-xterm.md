# Terminal Sessions — Phase 4: Dashboard xterm.js View — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a live + replay terminal screen for `kind === "terminal"` sessions in the dashboard session debugger, using xterm.js fed by the recorded `terminal_output` deltas.

**Architecture:** A terminal session's JSONL stream already carries `terminal_output` events whose `data` field is a **raw output delta with ANSI escape sequences preserved** (confirmed empirically — see "Key fact" below). xterm.js does its own ANSI emulation, so we feed each `data` delta straight into `term.write(delta)`. **No backend change is needed.** The session-detail page (`session.ts`) gets a `kind === "terminal"` branch that delegates to a new, self-contained `bootTerminalSession()` (slim layout: header + xterm screen + action timeline + footer) rather than threading terminal conditionals through the browser boot path. The xterm dependency is wrapped in an injectable seam (`terminalFactory`) so the logic is unit-testable in jsdom without a real renderer, mirroring the existing `webSocketCtor` injection pattern.

**Tech Stack:** TypeScript SPA (Vite multi-entry, flat output), Vitest, `@xterm/xterm` (new dependency). Frontend builds to the gitignored `src/octowright/server/frontend/`; only TS source is committed.

**View semantics (decisions locked for this phase):**
- **Read-only / output-only.** The dashboard xterm is display-only (`disableStdin: true`, `cursorBlink: false`). It shows program **output** (`terminal_output` deltas). It does **not** send keystrokes to the terminal and does **not** echo recorded `terminal_input`. This resolves spec §10 item 3 (PTY `ECHO`-off input echo): there is no dashboard-originated input to echo, and the agent's typed inputs remain visible in the **action timeline** (which renders every JSONL row, `terminal_input` included). *Rejected alternative:* self-echoing `terminal_input` into the xterm — risks interleaving incorrectly with output ordering, and YAGNI for a monitoring view.
- **Live and replay use the same feed.** Live: the existing `/tail` WS already streams new JSONL rows; we additionally route `terminal_output` deltas into the xterm. Replay (closed session): `GET …/events` returns all rows including `terminal_output`; we feed them in order into a fresh xterm. Deltas concatenate to the full raw stream, so both paths reconstruct the screen.
- **Fixed 80×24 with scrollback.** The PTY default size is 80×24; SSH fixes its own size. We construct xterm at 80×24 with scrollback rather than adding `@xterm/addon-fit` (one fewer dependency, no layout-measurement flakiness in tests). Exposing actual cols/rows is a future nicety, not in scope.

**Key fact (characterized empirically against a real `/bin/bash` PTY connector):** the connector's `snapshot.screen` (and therefore each `terminal_output.data` delta) is the **UTF-8-decoded raw output stream with ANSI escapes intact** — e.g. `'\x1b[?1034h\r\n…bash-3.2$ AB\x1b[31mRED\x1b[0m\r\n'`. CRLF line endings are present, so xterm must be built with `convertEol: false`. The original design doc (§93/§196/§317) speculated a base64 `bytes_b64` field; Phase 1 instead shipped a decoded `data` string that already preserves the escapes, so **no base64 round-trip and no `[emulator]`/pyte dependency are needed** — `term.write(data)` is sufficient.

**Known minor limitations (acceptable for a monitoring view; document, do not fix here):**
- If the connector's ~32KB cumulative buffer slides or is cleared in a way that `translate.py._delta` resolves to a non-append, a prompt line may render twice. In practice a `clear` emits `\x1b[H\x1b[2J` bytes *into* the append-only stream, so xterm executes a real clear; this is cosmetic and rare.
- A multibyte UTF-8 char could in theory split across a snapshot boundary; xterm tolerates stray bytes. Not observed in practice.

---

## File Structure

| Path | Create/Modify | Responsibility |
|------|---------------|----------------|
| `packages/octowright-frontend/package.json` | Modify | Add `@xterm/xterm` dependency |
| `packages/octowright-frontend/src/types.ts` | Modify | Widen `Kind` to include `"terminal"`; add `connector_type?` to `SessionDetail` |
| `packages/octowright-frontend/src/terminal-view.ts` | Create | xterm.js wrapper: `mountTerminalView()` → `{ write, reset, feedEvents, destroy }`, injectable factory |
| `packages/octowright-frontend/src/terminal-view.test.ts` | Create | Unit tests with a fake terminal (no real xterm in jsdom) |
| `packages/octowright-frontend/src/session-terminal.ts` | Create | `bootTerminalSession()` + `buildTerminalLayout()` — slim terminal detail page |
| `packages/octowright-frontend/src/session-terminal.test.ts` | Create | Boot tests: layout, history replay, live tail (injected WS + terminal) |
| `packages/octowright-frontend/src/session.ts` | Modify | Branch `bootSession` on `kind === "terminal"` → delegate to `bootTerminalSession` |
| `packages/octowright-frontend/src/session.test.ts` | Modify | Add a test asserting the terminal-kind branch delegates |
| `packages/octowright-frontend/static/styles.css` | Modify | `.kind-icon--terminal` color + `.session-terminal-view` container styling |
| `AGENTS.md` / `CLAUDE.md` | Modify | Document the dashboard xterm.js view + `@xterm/xterm` in the Frontend section |

---

## Task 1: Add `@xterm/xterm` dependency + widen frontend types

**Files:**
- Modify: `packages/octowright-frontend/package.json`
- Modify: `packages/octowright-frontend/src/types.ts:1` (the `Kind` type) and `:43-56` (`SessionDetail`)

- [ ] **Step 1: Add the dependency to package.json**

In `packages/octowright-frontend/package.json`, add `@xterm/xterm` to the `dependencies` block (keep the existing `@provide-io/telemetry` entry):

```json
  "dependencies": {
    "@provide-io/telemetry": "^0.4.7",
    "@xterm/xterm": "^5.5.0"
  }
```

- [ ] **Step 2: Install it**

Run: `cd packages/octowright-frontend && npm install`
Expected: `@xterm/xterm` resolves and `package-lock.json` updates; no errors.

- [ ] **Step 3: Widen `Kind` and extend `SessionDetail` in types.ts**

Change line 1 of `types.ts` from:

```ts
export type Kind = "chromium" | "firefox" | "webkit";
```

to (terminal sessions report `kind: "terminal"` from the backend):

```ts
export type Kind = "chromium" | "firefox" | "webkit" | "terminal";
```

Then add a `connector_type` field to the `SessionDetail` interface (after `macro_intent?`):

```ts
export interface SessionDetail extends SessionSummary {
  video_path: string | null;
  trace_path: string | null;
  markdown_path: string | null;
  websocket_path: string | null;
  event_count: number;
  action_count: number;
  console_count: number;
  download_count: number;
  page_count: number;
  cache: CacheReport;
  aria?: string;
  macro_intent?: string;
  /** "pty" | "ssh" for terminal sessions; absent/null for browsers. */
  connector_type?: "pty" | "ssh" | null;
}
```

- [ ] **Step 4: Typecheck — verify the widening has no fallout**

Run: `cd packages/octowright-frontend && npm run typecheck`
Expected: PASS. (`Kind` is used only as a field annotation in `types.ts`; there are no exhaustive `switch (kind)` statements in `src/`, so widening is safe.)

- [ ] **Step 5: Commit**

```bash
git add packages/octowright-frontend/package.json packages/octowright-frontend/package-lock.json packages/octowright-frontend/src/types.ts
git commit -m "feat(frontend): add @xterm/xterm dep and terminal kind to types"
```

---

## Task 2: `terminal-view.ts` — the xterm.js wrapper (TDD)

**Files:**
- Create: `packages/octowright-frontend/src/terminal-view.ts`
- Test: `packages/octowright-frontend/src/terminal-view.test.ts`

- [ ] **Step 1: Write the failing test**

Create `packages/octowright-frontend/src/terminal-view.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mountTerminalView, type TerminalLike } from "./terminal-view.js";
import type { RecordingEvent } from "./types.js";

class FakeTerminal implements TerminalLike {
  writes: string[] = [];
  resets = 0;
  disposes = 0;
  openedOn: HTMLElement | null = null;
  open(el: HTMLElement): void {
    this.openedOn = el;
  }
  write(data: string): void {
    this.writes.push(data);
  }
  reset(): void {
    this.resets += 1;
  }
  dispose(): void {
    this.disposes += 1;
  }
}

let container: HTMLDivElement;
let fake: FakeTerminal;
beforeEach(() => {
  container = document.createElement("div");
  fake = new FakeTerminal();
});

function mount() {
  return mountTerminalView(container, { sessionId: "t1", terminalFactory: () => fake });
}

describe("mountTerminalView", () => {
  it("opens the terminal onto a child of the container", () => {
    mount();
    expect(fake.openedOn).not.toBeNull();
    expect(container.contains(fake.openedOn)).toBe(true);
    expect(container.getAttribute("data-testid")).toBe("terminal-view");
  });

  it("write() forwards to the terminal", () => {
    const view = mount();
    view.write("hello");
    expect(fake.writes).toEqual(["hello"]);
  });

  it("feedEvents writes only terminal_output deltas, in order", () => {
    const view = mount();
    const events: RecordingEvent[] = [
      { ts: "t", action: "terminal_start", connector_type: "pty" },
      { ts: "t", action: "terminal_output", data: "AB" },
      { ts: "t", action: "terminal_input", value: "ls\n" },
      { ts: "t", action: "terminal_output", data: "\x1b[31mC\x1b[0m" },
      { ts: "t", action: "terminal_stop", reason: "eof" },
    ];
    view.feedEvents(events);
    expect(fake.writes).toEqual(["AB", "\x1b[31mC\x1b[0m"]);
  });

  it("feedEvents skips terminal_output rows whose data is not a string", () => {
    const view = mount();
    view.feedEvents([{ ts: "t", action: "terminal_output" }]);
    expect(fake.writes).toEqual([]);
  });

  it("destroy() disposes the terminal and clears the container", () => {
    const view = mount();
    view.destroy();
    expect(fake.disposes).toBe(1);
    expect(container.innerHTML).toBe("");
  });

  it("falls back to the real xterm Terminal when no factory is injected", () => {
    // Smoke: the default path constructs without throwing in jsdom. xterm's
    // renderer may warn but mountTerminalView must not throw at construction.
    expect(() => mountTerminalView(container, { sessionId: "t1" })).not.toThrow();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd packages/octowright-frontend && npx vitest run src/terminal-view.test.ts`
Expected: FAIL — cannot import `mountTerminalView` from `./terminal-view.js` (module does not exist).

- [ ] **Step 3: Write the implementation**

Create `packages/octowright-frontend/src/terminal-view.ts`:

```ts
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd packages/octowright-frontend && npx vitest run src/terminal-view.test.ts`
Expected: PASS (6 tests). If the "real xterm" smoke test throws in jsdom, see the note below.

> **jsdom note:** xterm.js v5 constructs cleanly in jsdom (it defers renderer work until `open()` measures the DOM). `term.open()` on a zero-size jsdom element is safe — it may log a benign warning but does not throw. If the default-factory smoke test proves flaky in CI, narrow it to assert only that `mountTerminalView` with an injected factory works, and delete the real-xterm smoke case; do **not** wrap the production `term.open()` in try/catch to paper over a real error.

- [ ] **Step 5: Commit**

```bash
git add packages/octowright-frontend/src/terminal-view.ts packages/octowright-frontend/src/terminal-view.test.ts
git commit -m "feat(frontend): terminal-view xterm wrapper fed by terminal_output deltas"
```

---

## Task 3: `session-terminal.ts` — the terminal boot path (TDD)

**Files:**
- Create: `packages/octowright-frontend/src/session-terminal.ts`
- Test: `packages/octowright-frontend/src/session-terminal.test.ts`

This module owns the slim terminal detail page and reuses `renderHeader`/`renderFooter` (from `session.ts`), `renderTimeline`/`appendTimelineEvents` (from `timeline.ts`), `openTail` (from `tail.ts`), and `mountTerminalView` (Task 2). It mocks the `api` module the same way other suites do.

- [ ] **Step 1: Write the failing test**

Create `packages/octowright-frontend/src/session-terminal.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RecordingEvent, SessionDetail } from "./types.js";
import type { TerminalLike } from "./terminal-view.js";

// Mock the api module so boot doesn't hit the network. getEvents returns the
// replay history; tailWebSocketUrl is a pure string builder.
const getEvents = vi.fn();
vi.mock("./api.js", () => ({
  getEvents: (...args: unknown[]) => getEvents(...args),
  tailWebSocketUrl: (id: string, since = 0) => `ws://test/${id}?since=${since}`,
}));

import { bootTerminalSession, buildTerminalLayout } from "./session-terminal.js";

class FakeTerminal implements TerminalLike {
  writes: string[] = [];
  open(): void {}
  write(d: string): void {
    this.writes.push(d);
  }
  reset(): void {}
  dispose(): void {}
}

interface Listener {
  type: string;
  handler: (e: unknown) => void;
}
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  listeners: Listener[] = [];
  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }
  addEventListener(type: string, handler: (e: unknown) => void): void {
    this.listeners.push({ type, handler });
  }
  emit(type: string, event: unknown): void {
    for (const l of this.listeners) if (l.type === type) l.handler(event);
  }
  close(): void {}
}

function makeDetail(overrides: Partial<SessionDetail> = {}): SessionDetail {
  return {
    id: "term-0",
    kind: "terminal",
    label: "ops shell",
    profile: "ops",
    url: null,
    started_at: "2026-06-12T12:00:00.000Z",
    live: false,
    log_path: "/tmp/term-0.jsonl",
    video_path: null,
    trace_path: null,
    markdown_path: null,
    websocket_path: null,
    event_count: 0,
    action_count: 0,
    console_count: 0,
    download_count: 0,
    page_count: 0,
    connector_type: "pty",
    cache: {
      total_bytes: 0,
      total_human: "0 B",
      components: {
        jsonl: { size_bytes: 0, size_human: "0 B", path: null, exists: false },
        markdown: { size_bytes: 0, size_human: "0 B", path: null, exists: false },
        trace: { size_bytes: 0, size_human: "0 B", path: null, exists: false },
        video: { size_bytes: 0, size_human: "0 B", path: null, exists: false },
        websocket: { size_bytes: 0, size_human: "0 B", path: null, exists: false },
        screenshots: { size_bytes: 0, size_human: "0 B", count: 0, paths: [] },
      },
      recommendations: [],
    },
    ...overrides,
  };
}

let root: HTMLDivElement;
let fakeTerm: FakeTerminal;
beforeEach(() => {
  root = document.createElement("div");
  fakeTerm = new FakeTerminal();
  getEvents.mockReset();
  FakeWebSocket.instances = [];
});

describe("buildTerminalLayout", () => {
  it("builds header, terminal slot, timeline, footer — and no video/tabs", () => {
    const refs = buildTerminalLayout(root);
    expect(refs.header).toBeTruthy();
    expect(refs.terminalSlot).toBeTruthy();
    expect(refs.timeline).toBeTruthy();
    expect(refs.footer).toBeTruthy();
    expect(root.querySelector('[data-testid="session-video"]')).toBeNull();
    expect(root.querySelector('[data-testid="session-tabs"]')).toBeNull();
  });
});

describe("bootTerminalSession", () => {
  it("replays recorded terminal_output deltas into the xterm (closed session)", async () => {
    const events: RecordingEvent[] = [
      { ts: "2026-06-12T12:00:00Z", action: "terminal_start", connector_type: "pty" },
      { ts: "2026-06-12T12:00:01Z", action: "terminal_output", data: "$ " },
      { ts: "2026-06-12T12:00:02Z", action: "terminal_output", data: "ls\r\nfile\r\n$ " },
    ];
    getEvents.mockResolvedValue({ events, cursor: 123, total_bytes: 0, complete: true });

    await bootTerminalSession(root, "term-0", makeDetail({ live: false }), {
      terminalFactory: () => fakeTerm,
    });

    expect(getEvents).toHaveBeenCalledWith("term-0", 0);
    expect(fakeTerm.writes).toEqual(["$ ", "ls\r\nfile\r\n$ "]);
    // Header reflects the terminal.
    expect(root.querySelector(".session-header__title")?.textContent).toBe("ops shell");
    // No live tail for a closed session.
    expect(FakeWebSocket.instances.length).toBe(0);
  });

  it("opens a live tail from the history cursor and writes new deltas (live session)", async () => {
    getEvents.mockResolvedValue({
      events: [{ ts: "2026-06-12T12:00:01Z", action: "terminal_output", data: "boot" }],
      cursor: 50,
      total_bytes: 0,
      complete: false,
    });

    await bootTerminalSession(root, "term-0", makeDetail({ live: true }), {
      terminalFactory: () => fakeTerm,
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });

    expect(fakeTerm.writes).toEqual(["boot"]); // replayed history
    const ws = FakeWebSocket.instances[0]!;
    expect(ws.url).toBe("ws://test/term-0?since=50"); // tail starts AFTER history
    ws.emit("message", {
      data: JSON.stringify({
        events: [{ ts: "2026-06-12T12:00:03Z", action: "terminal_output", data: "live-delta" }],
        cursor: 80,
      }),
    });
    expect(fakeTerm.writes).toEqual(["boot", "live-delta"]); // live delta appended
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd packages/octowright-frontend && npx vitest run src/session-terminal.test.ts`
Expected: FAIL — cannot import from `./session-terminal.js`.

- [ ] **Step 3: Write the implementation**

Create `packages/octowright-frontend/src/session-terminal.ts`:

```ts
// Terminal session detail page. A self-contained boot path for kind ===
// "terminal", kept separate from the browser bootSession so the browser path
// stays untouched. Slim layout: header + xterm screen + action timeline.

import { getEvents, tailWebSocketUrl } from "./api.js";
import { renderFooter, renderHeader } from "./session.js";
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd packages/octowright-frontend && npx vitest run src/session-terminal.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/octowright-frontend/src/session-terminal.ts packages/octowright-frontend/src/session-terminal.test.ts
git commit -m "feat(frontend): bootTerminalSession — live + replay terminal detail page"
```

---

## Task 4: Branch `session.ts` boot on terminal kind

**Files:**
- Modify: `packages/octowright-frontend/src/session.ts:521` (`bootSession`)
- Modify: `packages/octowright-frontend/src/session.test.ts`

- [ ] **Step 1: Write the failing test**

Add to `packages/octowright-frontend/src/session.test.ts`. First, at the top of the file, mock the terminal-boot module and the api module's `getSession` so we can assert delegation without a real network or xterm. Add these mocks **after** the existing imports:

```ts
import { vi } from "vitest";

const bootTerminalSession = vi.fn().mockResolvedValue(undefined);
vi.mock("./session-terminal.js", () => ({
  bootTerminalSession: (...args: unknown[]) => bootTerminalSession(...args),
  buildTerminalLayout: () => ({}),
}));
```

> If `session.test.ts` already imports `vi` and/or mocks `./api.js`, fold these into the existing statements rather than duplicating. `getSession` must be mockable; if the suite does not already mock `./api.js`, add a mock that returns a terminal-kind detail for the delegation test (reuse the `makeDetail` factory shape but with `kind: "terminal"`).

Then add the test:

```ts
describe("bootSession terminal branch", () => {
  it("delegates to bootTerminalSession when kind is terminal", async () => {
    bootTerminalSession.mockClear();
    const { getSession } = await import("./api.js");
    (getSession as ReturnType<typeof vi.fn>).mockResolvedValue(makeDetail({ kind: "terminal", live: false }));
    const el = document.createElement("div");
    const { bootSession } = await import("./session.js");
    await bootSession(el, "term-0");
    expect(bootTerminalSession).toHaveBeenCalledTimes(1);
    expect(bootTerminalSession.mock.calls[0]?.[1]).toBe("term-0");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd packages/octowright-frontend && npx vitest run src/session.test.ts`
Expected: FAIL — `bootSession` does not call `bootTerminalSession` (it runs the browser path against a terminal detail).

- [ ] **Step 3: Add the branch in `session.ts`**

Add the import near the other local imports (after line 18, `import { openTail } from "./tail.js";`):

```ts
import { bootTerminalSession } from "./session-terminal.js";
```

Then in `bootSession`, immediately after the `detail` is fetched and the `session_detail_loaded` log call (around line 533, **before** `renderHeader(refs.header, detail)`), insert:

```ts
  // Terminal sessions get a dedicated slim layout (no video/trace/tabs). Branch
  // before the browser layout build so the browser path stays untouched.
  if (detail.kind === "terminal") {
    await bootTerminalSession(root, sessionId, detail, {
      ...(opts.webSocketCtor ? { webSocketCtor: opts.webSocketCtor } : {}),
    });
    log.info({ event: "session_boot_complete", session_id: sessionId, kind: "terminal" });
    return;
  }
```

(The existing `buildLayout(root)` call at line 523 runs *before* `getSession`. Move it to *after* the terminal branch, or guard it — simplest: relocate `const refs = buildLayout(root);` to just after the terminal `return` block so a terminal session never builds the browser layout. Verify the browser tests still pass in Step 4.)

- [ ] **Step 4: Run the full frontend suite to verify pass + no regressions**

Run: `cd packages/octowright-frontend && npm run test:nocov`
Expected: PASS — all suites green (existing browser-session tests unaffected, new terminal branch test passes).

- [ ] **Step 5: Commit**

```bash
git add packages/octowright-frontend/src/session.ts packages/octowright-frontend/src/session.test.ts
git commit -m "feat(frontend): route terminal-kind sessions to the xterm detail page"
```

---

## Task 5: CSS — terminal icon color + screen container

**Files:**
- Modify: `packages/octowright-frontend/static/styles.css:881-883` (kind-icon colors) and append a terminal-view block

- [ ] **Step 1: Add the terminal kind-icon color**

After line 883 (`.kind-icon--webkit   { color: var(--fg-2); }`), add:

```css
.kind-icon--terminal { color: #4ade80; }
```

- [ ] **Step 2: Add the terminal screen container styling**

Append to `styles.css` (the xterm.css imported by `terminal-view.ts` handles the cell grid/cursor; this just frames the container):

```css
/* Terminal session detail — frames the xterm screen. xterm.css (imported by
   terminal-view.ts) styles the rows/cursor themselves. */
.session-terminal {
  margin: 0.5rem 0;
}
.session-terminal-view {
  background: #0b0b0e;
  border: 1px solid var(--border, #333);
  border-radius: 6px;
  padding: 6px;
  overflow: hidden;
}
.session-terminal-view__screen {
  width: 100%;
  min-height: 360px;
}
.session-page--terminal {
  display: block;
}
```

- [ ] **Step 3: Commit**

```bash
git add packages/octowright-frontend/static/styles.css
git commit -m "style(frontend): terminal kind-icon color + xterm screen container"
```

---

## Task 6: Build the frontend artifact + live smoke

This task produces the built artifact (gitignored — not committed) and verifies it renders against a real terminal. No source changes; it is a verification gate.

- [ ] **Step 1: Full frontend test suite (with coverage gate)**

Run: `cd packages/octowright-frontend && npm run test`
Expected: PASS, coverage thresholds met.

- [ ] **Step 2: Production build**

Run: `cd packages/octowright-frontend && npm run build`
Expected: Vite build succeeds; `session.js` (now including the terminal modules + xterm), `xterm.css`, and the HTML land in `src/octowright/server/frontend/`. Confirm `xterm.css` was emitted and linked into the built `session.html` (Vite injects a `<link>` for CSS imported in the entry graph).

- [ ] **Step 3: Live smoke (manual, requires the `[terminal]` extra)**

Run a daemon and launch a PTY terminal, then open the session debugger:

```bash
uv run --active octowright serve &        # or use an already-running daemon
# In an MCP client (or via the dashboard), launch a terminal:
#   terminal_launch(kind="pty", command="/bin/bash")
# then drive a few commands (terminal_send_input "ls\n", "printf '\\033[31mRED\\033[0m\\n'")
```

Open `http://127.0.0.1:6286/sessions/<terminal-instance-id>` and confirm:
- The header shows the terminal label and a green "T" kind-icon, status LIVE.
- The xterm screen shows the shell output, **with ANSI color** (the RED text is red).
- The action timeline lists `terminal_start` / `terminal_output` / `terminal_input` rows.
- Closing the terminal (or reopening the URL after close) replays the recorded output into the xterm (status CLOSED, no live tail).

> This step does not produce a commit. If the build or smoke reveals an issue, fix it in the relevant earlier task's files and re-commit there.

---

## Task 7: Docs — AGENTS.md / CLAUDE.md

**Files:**
- Modify: `AGENTS.md` (the **Terminal Sessions (optional) → Dashboard** paragraph and the **Frontend** section)
- Then: `cp AGENTS.md CLAUDE.md`

AGENTS.md is canonical; CLAUDE.md must be a byte-identical copy (enforced by `scripts/check_agent_docs_sync.py`).

- [ ] **Step 1: Update the Terminal Sessions "Dashboard." paragraph**

Find the sentence in the **Terminal Sessions (optional)** section that begins **"Dashboard. `http/state.py` re-exports `terminal_pool`…"** and append, after the existing text about the terminal-shaped detail:

> The session-detail page renders a **read-only xterm.js terminal screen** for `kind === "terminal"`: `session.ts` branches to `bootTerminalSession` (`session-terminal.ts`), which mounts an xterm instance (`terminal-view.ts`) fed by the recorded `terminal_output` deltas — live via the `/tail` WebSocket and replayed from `GET …/events` for closed sessions. The view is output-only (it never sends keystrokes and does not echo recorded `terminal_input`; typed input stays visible in the action timeline). Each `terminal_output.data` delta is the raw output stream with ANSI escapes preserved, so it is written verbatim into xterm, which does its own emulation — no base64/pyte dependency.

- [ ] **Step 2: Update the Frontend section**

In the **### Frontend** section, add a sentence noting the new dependency and modules:

> Terminal sessions render with **`@xterm/xterm`** (added as a direct dependency): `terminal-view.ts` wraps an xterm instance behind an injectable factory (unit-testable without a real renderer), and `session-terminal.ts` is the terminal detail boot path.

- [ ] **Step 3: Sync CLAUDE.md**

Run: `cp AGENTS.md CLAUDE.md`

- [ ] **Step 4: Verify the docs-sync gate**

Run: `uv run --active python scripts/check_agent_docs_sync.py`
Expected: PASS (files identical).

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md CLAUDE.md
git commit -m "docs(terminal): document the dashboard xterm.js terminal view"
```

---

## Self-Review (completed during planning)

**Spec coverage** (against the Phase 4 roadmap entry + design §10 item 3):
- ✅ Add `@xterm/xterm` to the frontend — Task 1.
- ✅ Render a terminal view for `kind === "terminal"` in the session debugger — Tasks 3–5.
- ✅ Live (subscribe `/tail`, write `terminal_output.data` deltas) — Task 3 (live branch).
- ✅ Replay (feed recorded deltas in order) — Task 3 (history replay).
- ✅ Decide PTY `ECHO`-off input-echo behavior — resolved to **read-only / output-only** (View semantics + Task 7 docs).
- ✅ Docs: terminal session in the dashboard + frontend tables — Task 7. (The "Five Concepts" terminal entry and the env-var/profile tables already exist from Phases 1–3, so no further table edits are needed; this is noted rather than duplicated.)

**Divergence reconciled:** design doc's `bytes_b64` raw-bytes assumption is superseded by Phase 1's decoded `data` string (ANSI preserved, characterized empirically); plan feeds `data` directly to `term.write()` — no backend change.

**Type consistency:** `mountTerminalView` / `TerminalLike` / `TerminalFactory` (Task 2) are used verbatim in Task 3's import; `bootTerminalSession(root, sessionId, detail, opts)` signature in Task 3 matches the call site in Task 4; `TerminalBootOptions.webSocketCtor`/`terminalFactory` names are consistent across Tasks 3–4; `getEvents`/`tailWebSocketUrl`/`renderTimeline`/`appendTimelineEvents`/`renderHeader`/`renderFooter`/`openTail` are all real existing exports (verified).

**No placeholders:** every code step contains complete code; commands have expected output.

---

## Execution Handoff

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (spec then quality) between tasks.
2. **Inline Execution** — execute tasks in this session with checkpoints.
