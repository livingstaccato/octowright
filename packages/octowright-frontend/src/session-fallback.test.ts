import { describe, expect, it } from "vitest";

import { mountFallbackStream } from "./session-fallback.js";

const ctx = { sessionId: "s1", live: false, kind: "refkind" };

describe("mountFallbackStream", () => {
  it("renders a visible reason for every trigger", () => {
    const triggers = [
      { code: "no-frontend" as const, match: /no renderer/i },
      { code: "version-mismatch" as const, match: /version/i },
      { code: "import-failed" as const, match: /load/i },
      { code: "mount-failed" as const, match: /render/i },
    ];
    for (const t of triggers) {
      const el = document.createElement("div");
      mountFallbackStream(el, ctx, { code: t.code, detail: "boom" });
      expect(el.textContent ?? "").toMatch(t.match);
    }
  });

  it("names the kind so an operator knows which plugin failed", () => {
    const el = document.createElement("div");
    mountFallbackStream(el, ctx, { code: "import-failed", detail: "404" });
    expect(el.textContent).toContain("refkind");
  });

  it("surfaces the underlying detail rather than swallowing it", () => {
    const el = document.createElement("div");
    mountFallbackStream(el, ctx, { code: "mount-failed", detail: "TypeError: x is not a function" });
    expect(el.textContent).toContain("TypeError: x is not a function");
  });

  it("still shows events, so a failed renderer is degraded not blank", () => {
    const el = document.createElement("div");
    const handle = mountFallbackStream(el, ctx, { code: "no-frontend", detail: "" });
    handle.feed([{ ts: "2026-08-24T00:00:00Z", action: "ref_ready" }]);
    expect(el.textContent).toContain("ref_ready");
  });

  it("destroy is idempotent", () => {
    const el = document.createElement("div");
    const handle = mountFallbackStream(el, ctx, { code: "no-frontend", detail: "" });
    handle.destroy();
    expect(() => handle.destroy()).not.toThrow();
  });
});
