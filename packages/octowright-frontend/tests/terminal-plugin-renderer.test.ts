// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * Runtime contract coverage for the terminal plugin's dashboard renderer.
 *
 * Mirrors `reference-plugin-renderer.test.ts`: a Python test
 * (`packages/octowright-terminal/tests/test_frontend_asset.py`) already
 * proves `renderer.js` is declared and contains the literal string
 * `"mountStream"`, but nothing there ever executes it. This test imports the
 * BUILT module (the actual bundled artifact the dashboard serves off disk,
 * not the TypeScript source) and drives it through the real
 * mount/feed/destroy cycle against the shapes `session-stream.ts` hands a
 * renderer today, so a signature drift -- `feed` renamed, `ctx.sessionId`
 * renamed, the reset-delta contract changing shape -- fails here instead of
 * only showing up as a silently-broken terminal pane in a real dashboard.
 *
 * Unlike the reference renderer, this one opens a REAL `@xterm/xterm`
 * `Terminal`, which needs two jsdom polyfills neither vitest's `jsdom`
 * environment nor this package's `test-setup.ts` provide:
 * `window.matchMedia` (xterm's `Terminal.open()` calls it directly -- see
 * `terminal-view.test.ts`'s note on why core's OWN pre-extraction tests
 * never exercised a real xterm instance) and `ResizeObserver` (xterm's
 * internal viewport sizing constructs one even though this renderer never
 * creates its own, unlike `terminal-view.ts`). Both are stubbed locally in
 * this file only -- not added to the shared `test-setup.ts` -- because
 * nothing else in this package needs them.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RENDERER_API_VERSION } from "../src/plugin-registry.js";

const RENDERER_PATH = resolve(__dirname, "../../octowright-terminal/src/octowright_terminal/assets/renderer.js");
const PLUGIN_PY_PATH = resolve(__dirname, "../../octowright-terminal/src/octowright_terminal/plugin.py");

interface TerminalSessionEvent {
  ts: string;
  action: string;
  data?: string;
  reset?: boolean;
  [field: string]: unknown;
}

interface TerminalStreamHandle {
  feed: (events: TerminalSessionEvent[]) => void;
  destroy: () => void;
}

type MountStreamFn = (
  el: HTMLElement,
  ctx: { sessionId: string; live: boolean; kind: string },
) => TerminalStreamHandle | Promise<TerminalStreamHandle>;

let matchMediaSpy: ReturnType<typeof vi.fn> | undefined;
let originalResizeObserver: typeof ResizeObserver | undefined;

beforeEach(() => {
  // xterm.js's Terminal.open() reads window.matchMedia (device-pixel-ratio
  // change detection) -- jsdom does not implement it at all.
  matchMediaSpy = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  window.matchMedia = matchMediaSpy as unknown as typeof window.matchMedia;

  // xterm's DOM renderer observes its container for size changes -- jsdom
  // has no ResizeObserver global at all.
  originalResizeObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
});

afterEach(() => {
  if (originalResizeObserver) {
    globalThis.ResizeObserver = originalResizeObserver;
  } else {
    // @ts-expect-error -- deliberately restoring "not defined" for a jsdom env
    delete globalThis.ResizeObserver;
  }
});

describe("terminal plugin renderer (runtime contract)", () => {
  it("mounts a real xterm instance, feeds two batches (one a reset), and destroys per the published StreamHandle contract", async () => {
    const mod = (await import(pathToFileURL(RENDERER_PATH).href)) as { mountStream: MountStreamFn };
    expect(typeof mod.mountStream).toBe("function");

    const el = document.createElement("div");
    document.body.appendChild(el);
    const ctx = { sessionId: "sess-term-1", live: false, kind: "terminal" };
    const handle = await mod.mountStream(el, ctx);

    // The mount tags its own root with the session id -- proves ctx reached
    // the renderer with the field name the contract promises.
    expect(el.getAttribute("data-terminal-stream")).toBe("sess-term-1");
    // A real xterm instance is live in the DOM, not a stub.
    expect(el.querySelector(".xterm")).not.toBeNull();

    // History arrives first, exactly as `bootStreamSession` feeds it: the
    // full recorded backlog in one batch before anything live.
    handle.feed([
      { ts: "2026-01-01T00:00:00Z", action: "terminal_start" },
      { ts: "2026-01-01T00:00:01Z", action: "terminal_output", data: "hello-history" },
    ]);
    await vi.waitFor(() => {
      expect(el.textContent).toContain("hello-history");
    });

    // At-least-once delivery: a second feed (a `/tail` reconnect replay in
    // the real dashboard) must not throw and re-writes its bytes rather than
    // deduping -- the honest behavior for a terminal, per plugin-contract.d.ts.
    handle.feed([{ ts: "2026-01-01T00:00:02Z", action: "terminal_output", data: "hello-history" }]);
    await vi.waitFor(() => {
      const count = (el.textContent?.match(/hello-history/g) ?? []).length;
      expect(count).toBeGreaterThanOrEqual(2);
    });

    // A `reset: true` delta clears the screen before writing its own data --
    // the OLD content must not survive it.
    handle.feed([{ ts: "2026-01-01T00:00:03Z", action: "terminal_output", data: "after-reset", reset: true }]);
    await vi.waitFor(() => {
      expect(el.textContent).toContain("after-reset");
      expect(el.textContent).not.toContain("hello-history");
    });

    // A non-terminal_output row (e.g. terminal_stop) and a non-string data
    // field must be silently skipped, not thrown on.
    expect(() =>
      handle.feed([
        { ts: "2026-01-01T00:00:04Z", action: "terminal_stop", reason: "eof" },
        { ts: "2026-01-01T00:00:05Z", action: "terminal_output", data: 42 as unknown as string },
      ]),
    ).not.toThrow();

    handle.destroy();
    expect(el.getAttribute("data-terminal-stream")).toBeNull();
    expect(el.querySelector(".xterm")).toBeNull();
  });

  it("keeps plugin.py's declared renderer_api_version in step with RENDERER_API_VERSION", () => {
    // Collapsing this check into "the plugin loads and renders" would not
    // catch a version bump on either side alone -- plugin-registry.ts's own
    // gate (resolveRenderer) would swap the terminal plugin's dashboard to
    // the version-mismatch fallback, which itself renders fine and would not
    // fail the mount/feed/destroy assertions above. Reading the Python
    // source's literal is a deliberate cross-language drift guard, not a
    // design compromise -- there is no runtime bridge between a vitest
    // process and the Python descriptor to call instead.
    const pluginPy = readFileSync(PLUGIN_PY_PATH, "utf-8");
    const match = /renderer_api_version\s*=\s*(\d+)/.exec(pluginPy);
    expect(match).not.toBeNull();
    expect(Number(match?.[1])).toBe(RENDERER_API_VERSION);
  });
});
