import { describe, expect, it, vi } from "vitest";
import { renderSessionTable } from "./session-table.js";
import type { SessionSummary } from "./types.js";

const row: SessionSummary = {
  id: "s/1",
  kind: "chromium",
  label: null,
  profile: null,
  url: null,
  started_at: "bad-date",
  live: false,
  log_path: "s1.jsonl",
};

describe("renderSessionTable", () => {
  it("renders live and closed empty states", () => {
    const actions = { onRelaunch: vi.fn(), onDelete: vi.fn() };
    expect(renderSessionTable([], true, actions).textContent).toBe("No live browsers.");
    expect(renderSessionTable([], false, actions).textContent).toBe("No closed sessions yet.");
  });

  it("renders null label/profile/url as empty cells", () => {
    const table = renderSessionTable([{ ...row, live: true }], true, { onRelaunch: vi.fn(), onDelete: vi.fn() });
    expect(table.querySelector("a")?.getAttribute("href")).toBe("/sessions/s%2F1");
    expect(table.textContent).toContain("bad-date");
  });

  it("shows lock badge and css class for protected sessions", () => {
    const table = renderSessionTable([{ ...row, live: true, protected: true }], true, {
      onRelaunch: vi.fn(),
      onDelete: vi.fn(),
    });
    expect(table.querySelector(".protected-badge")).not.toBeNull();
    expect(table.querySelector("tr.protected-session")).not.toBeNull();
  });

  it("shows no lock badge for unprotected sessions", () => {
    const table = renderSessionTable([{ ...row, live: true, protected: false }], true, {
      onRelaunch: vi.fn(),
      onDelete: vi.fn(),
    });
    expect(table.querySelector(".protected-badge")).toBeNull();
    expect(table.querySelector("tr.protected-session")).toBeNull();
  });

  it("wires closed-session relaunch and delete buttons", () => {
    const onRelaunch = vi.fn();
    const onDelete = vi.fn();
    const table = renderSessionTable([row], false, { onRelaunch, onDelete });

    table.querySelector<HTMLButtonElement>('[aria-label="Relaunch with same params"]')?.click();
    table.querySelector<HTMLButtonElement>('[aria-label="Delete recording"]')?.click();

    expect(onRelaunch).toHaveBeenCalledWith("s/1");
    expect(onDelete).toHaveBeenCalledWith("s/1");
  });
});
