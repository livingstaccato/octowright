import { describe, expect, it, vi } from "vitest";

import { RENDERER_API_VERSION, loadPluginRegistry, resolveRenderer } from "./plugin-registry.js";

function fakeFetch(body: unknown, ok = true) {
  return vi.fn().mockResolvedValue({ ok, json: async () => body });
}

describe("loadPluginRegistry", () => {
  it("maps kind to its renderer descriptor", async () => {
    const reg = await loadPluginRegistry(
      fakeFetch({
        refkind: {
          moduleUrl: "/plugins/p/renderer.js",
          rendererApiVersion: RENDERER_API_VERSION,
          displayName: "Ref",
          layout: "stream",
        },
      }) as never,
    );
    expect(reg.get("refkind")?.moduleUrl).toBe("/plugins/p/renderer.js");
  });

  it("is empty rather than throwing when the endpoint fails", async () => {
    const reg = await loadPluginRegistry(vi.fn().mockRejectedValue(new Error("offline")) as never);
    expect(reg.size).toBe(0);
  });

  it("is empty rather than throwing on a non-ok response", async () => {
    const reg = await loadPluginRegistry(fakeFetch({}, false) as never);
    expect(reg.size).toBe(0);
  });

  it("is empty rather than throwing on malformed JSON", async () => {
    const malformed = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => {
        throw new SyntaxError("Unexpected token in JSON");
      },
    });
    const reg = await loadPluginRegistry(malformed as never);
    expect(reg.size).toBe(0);
  });
});

describe("resolveRenderer", () => {
  const entry = (version: number) => ({
    moduleUrl: "/plugins/p/renderer.js",
    rendererApiVersion: version,
    displayName: "Ref",
    layout: "stream" as const,
  });

  it("resolves a matching version", () => {
    const reg = new Map([["refkind", entry(RENDERER_API_VERSION)]]);
    expect(resolveRenderer(reg, "refkind")).toEqual({
      moduleUrl: "/plugins/p/renderer.js",
      layout: "stream",
    });
  });

  it("refuses a mismatched version with that reason", () => {
    const reg = new Map([["refkind", entry(RENDERER_API_VERSION + 1)]]);
    const out = resolveRenderer(reg, "refkind");
    expect(out).toMatchObject({ code: "version-mismatch" });
    expect((out as { detail: string }).detail).toContain(String(RENDERER_API_VERSION));
  });

  it("reports no-frontend for an unknown kind", () => {
    expect(resolveRenderer(new Map(), "nosuch")).toMatchObject({ code: "no-frontend" });
  });

  it("reports no-frontend for a plugin declaring the browser layout", () => {
    const reg = new Map([
      [
        "refkind",
        {
          moduleUrl: "/plugins/p/renderer.js",
          rendererApiVersion: RENDERER_API_VERSION,
          displayName: "Ref",
          layout: "browser" as const,
        },
      ],
    ]);
    const out = resolveRenderer(reg, "refkind");
    expect(out).toMatchObject({ code: "no-frontend" });
    expect((out as { detail: string }).detail).toContain("browser");
  });
});
