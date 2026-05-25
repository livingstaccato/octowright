import { describe, expect, it, vi } from "vitest";
import { renderPersonaGrid } from "./persona-grid.js";
import type { PersonaSummary } from "./types.js";

const persona: PersonaSummary = {
  name: "qa",
  display_name: null,
  engines: [],
  last_used: "bad-date",
};

describe("renderPersonaGrid", () => {
  it("renders an empty placeholder", () => {
    expect(
      renderPersonaGrid([], { sizesProvider: () => ({ sizes: {}, loaded: false }), onEdit: vi.fn() }).textContent,
    ).toBe("No personas saved.");
  });

  it("renders unloaded, missing, and byte-backed sizes", () => {
    const loading = renderPersonaGrid([persona], {
      sizesProvider: () => ({ sizes: {}, loaded: false }),
      onEdit: vi.fn(),
    });
    expect(loading.querySelector(".persona-card__size")?.textContent).toBe("…");
    expect(loading.textContent).toContain("qa");
    expect(loading.textContent).toContain("no engines");

    const loadedMissing = renderPersonaGrid([persona], {
      sizesProvider: () => ({ sizes: { qa: null }, loaded: true }),
      onEdit: vi.fn(),
    });
    expect(loadedMissing.querySelector(".persona-card__size")?.textContent).toBe("—");

    const loadedBytes = renderPersonaGrid([{ ...persona, display_name: "QA", engines: ["chromium"] }], {
      sizesProvider: () => ({ sizes: { qa: 2048 }, loaded: true }),
      onEdit: vi.fn(),
    });
    expect(loadedBytes.textContent).toContain("QA");
    expect(loadedBytes.textContent).toContain("chromium");
    expect(loadedBytes.querySelector(".persona-card__size")?.textContent).toBe("2 KB");
  });

  it("wires the edit button", () => {
    const onEdit = vi.fn();
    const grid = renderPersonaGrid([persona], {
      sizesProvider: () => ({ sizes: {}, loaded: false }),
      onEdit,
    });

    grid.querySelector<HTMLButtonElement>('[aria-label="Edit persona qa"]')?.click();

    expect(onEdit).toHaveBeenCalledWith("qa");
  });
});
