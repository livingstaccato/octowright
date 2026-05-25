import { describe, expect, it, vi } from "vitest";
import { renderMacroList } from "./macro-list.js";
import type { DashboardState } from "./dashboard-state.js";

const emptyState: DashboardState = {
  sessions: { live: [], closed: [] },
  scenarios: { live: [] },
  personas: [],
  macros: [],
};

describe("renderMacroList", () => {
  it("renders an empty placeholder", () => {
    expect(renderMacroList(emptyState, { onEdit: vi.fn(), onRepairPreview: vi.fn() }).textContent).toBe(
      "No macros saved.",
    );
  });

  it("renders rows without description or params and wires actions", () => {
    const onEdit = vi.fn();
    const onRepairPreview = vi.fn();
    const state: DashboardState = {
      ...emptyState,
      sessions: {
        live: [
          {
            id: "s1",
            kind: "chromium",
            label: null,
            profile: null,
            url: null,
            started_at: "2026-05-01T00:00:00Z",
            live: true,
            log_path: "s1.jsonl",
          },
        ],
        closed: [],
      },
      macros: [{ name: "login", description: null, parameters: [], updated_at: null }],
    };

    const list = renderMacroList(state, { onEdit, onRepairPreview });
    expect(list.querySelector(".macro-list__desc")?.textContent).toBe("");
    expect(list.querySelector(".macro-list__params")).toBeNull();

    list.querySelector<HTMLButtonElement>('[data-testid="macro-edit-login"]')?.click();
    list.querySelector<HTMLButtonElement>('[data-testid="macro-repair-preview-login"]')?.click();

    expect(onEdit).toHaveBeenCalledWith("login", state.sessions);
    expect(onRepairPreview).toHaveBeenCalledWith("login");
  });
});
