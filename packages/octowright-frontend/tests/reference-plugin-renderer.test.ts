// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * Runtime contract coverage for the reference plugin's dashboard renderer.
 *
 * `tests/plugins/test_reference_frontend.py` (Python) already proves
 * `renderer.js` is SERVED and contains the literal string `"mountStream"` --
 * but nothing there ever executes it. That leaves CI blind to the one thing
 * that actually matters: whether the file still honors the published
 * `plugin-contract.d.ts` shape. A CI-green suite would not have caught
 * `feed` being renamed to `push`, `ctx.sessionId` becoming `ctx.session_id`,
 * `mountStream`'s parameters being reordered, `SessionEvent.action`
 * changing, or `RENDERER_API_VERSION` (plugin-registry.ts) being bumped
 * without a matching bump to `renderer_api_version` in
 * `tests/plugins/reference/plugin.py` -- that last one would make the
 * reference plugin's own dashboard render `version-mismatch` with the whole
 * suite green.
 *
 * WHAT THIS CLOSES: runtime drift -- does `mountStream(el, ctx)` actually
 * return a working `{ feed, destroy }` handle against the shapes this
 * dashboard hands a renderer today.
 *
 * WHAT THIS DOES NOT CLOSE: type-level drift. `renderer.js` lives outside
 * `rootDir: "src"` (tsconfig.json), so it cannot join the `tsc` program --
 * attempting to `include` it produces TS6059 ("File is not under 'rootDir'").
 * A signature change that is still call-compatible at runtime (e.g. an added
 * optional parameter) would pass this test and `tsc --noEmit` alike while
 * still not being checked against `plugin-contract.d.ts` by either. Do not
 * read a green run here as proof the file type-checks -- it never has, and
 * this test does not change that.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { describe, expect, it } from "vitest";

import { RENDERER_API_VERSION } from "../src/plugin-registry.js";

const RENDERER_PATH = resolve(__dirname, "../../../tests/plugins/reference/assets/renderer.js");
const PLUGIN_PY_PATH = resolve(__dirname, "../../../tests/plugins/reference/plugin.py");

describe("reference plugin renderer (runtime contract)", () => {
  it("mounts, feeds two batches, and destroys per the published StreamHandle contract", async () => {
    const mod = (await import(pathToFileURL(RENDERER_PATH).href)) as {
      mountStream: (
        el: HTMLElement,
        ctx: { sessionId: string; live: boolean; kind: string },
      ) => { feed: (events: unknown[]) => void; destroy: () => void };
    };
    expect(typeof mod.mountStream).toBe("function");

    const el = document.createElement("div");
    const ctx = { sessionId: "sess-ref-1", live: false, kind: "refkind" };
    const handle = await mod.mountStream(el, ctx);

    // The mount tags its own log element with the session id -- proves ctx
    // reached the renderer with the field name the contract promises.
    expect(el.querySelector('[data-refkind-stream="sess-ref-1"]')).not.toBeNull();

    handle.feed([{ ts: "2026-01-01T00:00:00Z", action: "refkind_launch" }]);
    expect(el.textContent).toContain("refkind_launch");

    // At-least-once delivery: a second feed (a `/tail` reconnect replay, in
    // the real dashboard) must not throw and must append rather than replace.
    handle.feed([{ ts: "2026-01-01T00:00:01Z", action: "refkind_close" }]);
    expect(el.textContent).toContain("refkind_launch");
    expect(el.textContent).toContain("refkind_close");

    handle.destroy();
    expect(el.querySelector('[data-refkind-stream="sess-ref-1"]')).toBeNull();
  });

  it("keeps plugin.py's declared renderer_api_version in step with RENDERER_API_VERSION", () => {
    // Collapsing this check into "the plugin loads and renders" would not
    // catch a version bump on either side alone: plugin-registry.ts's own
    // gate (resolveRenderer) would swap the reference plugin's dashboard to
    // the version-mismatch fallback, which itself renders fine and would not
    // fail this test's mount/feed/destroy assertions above. Reading the
    // Python source's literal is a deliberate cross-language drift guard,
    // not a design compromise -- there is no runtime bridge between a
    // vitest process and the Python descriptor to call instead.
    const pluginPy = readFileSync(PLUGIN_PY_PATH, "utf-8");
    const match = /renderer_api_version\s*=\s*(\d+)/.exec(pluginPy);
    expect(match).not.toBeNull();
    expect(Number(match?.[1])).toBe(RENDERER_API_VERSION);
  });
});
