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
let fitMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  container = document.createElement("div");
  fake = new FakeTerminal();
  fitMock = vi.fn();
});

function mount() {
  return mountTerminalView(container, {
    sessionId: "t1",
    terminalFactory: () => ({ terminal: fake, fit: fitMock }),
  });
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

  it("fits the terminal to its container on mount", () => {
    mount();
    expect(fitMock).toHaveBeenCalledTimes(1);
  });

  it("fit() refits on demand", () => {
    const view = mount();
    fitMock.mockClear();
    view.fit();
    expect(fitMock).toHaveBeenCalledTimes(1);
  });

  it("destroy() disposes the terminal and clears the container", () => {
    const view = mount();
    view.destroy();
    expect(fake.disposes).toBe(1);
    expect(container.innerHTML).toBe("");
  });

  // NOTE: the real-xterm default factory is intentionally NOT smoke-tested here.
  // xterm's Terminal.open() calls window.matchMedia, which jsdom doesn't
  // implement, so it throws only in the test environment — not in a real
  // browser. The injected-factory tests above cover the wiring; the real
  // default factory is exercised by the live dashboard smoke (Phase 4 Task 6).
});
