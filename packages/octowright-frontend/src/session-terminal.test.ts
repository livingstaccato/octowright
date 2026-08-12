import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RecordingEvent, SessionDetail } from "./types.js";
import type { TerminalLike } from "./terminal-view.js";

// Mock the api module so boot doesn't hit the network. session-terminal.ts
// transitively imports session.ts (for renderHeader/renderFooter), which
// imports many other api exports — so spread the real module and override only
// getEvents (spied) and tailWebSocketUrl (deterministic for the assertion).
const getEvents = vi.fn();
vi.mock("./api.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api.js")>()),
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
      terminalFactory: () => ({ terminal: fakeTerm, fit: () => {} }),
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
      terminalFactory: () => ({ terminal: fakeTerm, fit: () => {} }),
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

  it("shows re-pair guidance when an established tail lease expires", async () => {
    getEvents.mockResolvedValue({ events: [], cursor: 0, total_bytes: 0, complete: false });
    await bootTerminalSession(root, "term-0", makeDetail({ live: true }), {
      terminalFactory: () => ({ terminal: fakeTerm, fit: () => {} }),
      webSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });

    FakeWebSocket.instances[0]?.emit("close", {
      code: 1008,
      reason: "dashboard pairing expired",
      wasClean: true,
    });

    expect(root.querySelector('[data-testid="dashboard-auth-required"]')?.textContent).toContain(
      "octowright dashboard",
    );
  });
});
