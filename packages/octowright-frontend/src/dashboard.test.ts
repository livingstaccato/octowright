import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderDashboard } from "./dashboard.js";
import type { LiveScenario, MacroSummary, PersonaSummary, SessionListResponse, ScenarioListResponse } from "./types.js";

vi.mock("./api.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api.js")>();
  return {
    ...actual,
    getMacroRepairPreview: vi.fn(async () => ({
      macro: "login",
      suggestions: [
        {
          macro: "login",
          action_index: 0,
          original_action: { action: "click", selector: "#submit" },
          replacement_action: { action: "click_by", text: "Submit" },
          action_preview: "Click by text 'Submit'",
          prompt: "Review selector '#submit'",
          source: "stored_heuristic",
        },
      ],
    })),
  };
});

let root: HTMLDivElement;
beforeEach(() => {
  root = document.createElement("div");
  document.body.append(root);
});

const sessions: SessionListResponse = {
  live: [
    {
      id: "L1",
      kind: "chromium",
      label: "live one",
      profile: null,
      url: "https://example.com",
      started_at: "2026-04-24T13:00:00Z",
      live: true,
      log_path: "x",
    },
  ],
  closed: [
    {
      id: "C1",
      kind: "firefox",
      label: null,
      profile: "default",
      url: null,
      started_at: "2026-04-23T08:00:00Z",
      live: false,
      log_path: "y",
    },
  ],
};

const scenarios: ScenarioListResponse = {
  live: [
    {
      scenario_id: "scn-1",
      name: "two-player",
      participants: [
        { role: "alice", persona: "shopper", kind: "chromium", instance_id: "L1" },
        { role: "bob", persona: "admin", kind: "firefox", instance_id: "L2" },
      ],
    } satisfies LiveScenario,
  ],
};

const personas: PersonaSummary[] = [
  { name: "shopper", display_name: "Shopper", engines: ["chromium"], last_used: "2026-04-24T12:00:00Z" },
];

const macros: MacroSummary[] = [
  { name: "login", description: "logs in", parameters: ["user"], updated_at: "2026-04-23T00:00:00Z" },
];

describe("renderDashboard", () => {
  it("renders all five panels", () => {
    renderDashboard(root, { sessions, scenarios, personas, macros });
    expect(root.querySelector('[data-testid="panel-live-browsers"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="panel-live-scenarios"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="panel-personas"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="panel-closed-sessions"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="panel-macros"]')).not.toBeNull();
  });
  it("renders live and closed session rows", () => {
    renderDashboard(root, { sessions, scenarios, personas, macros });
    const live = root.querySelector('[data-testid="table-live-sessions"]');
    expect(live?.querySelectorAll("tbody tr").length).toBe(1);
    const closed = root.querySelector('[data-testid="table-closed-sessions"]');
    expect(closed?.querySelectorAll("tbody tr").length).toBe(1);
  });
  it("renders scenario chips with links", () => {
    renderDashboard(root, { sessions, scenarios, personas, macros });
    const chips = root.querySelectorAll('[data-testid="panel-live-scenarios"] a.chip');
    expect(chips.length).toBe(2);
    expect((chips[0] as HTMLAnchorElement).href).toContain("/sessions/L1");
  });
  it("shows empty placeholders when nothing is provided", () => {
    renderDashboard(root, {
      sessions: { live: [], closed: [] },
      scenarios: { live: [] },
      personas: [],
      macros: [],
    });
    const empties = root.querySelectorAll(".empty");
    expect(empties.length).toBeGreaterThanOrEqual(4);
  });
  it("opens a non-mutating macro repair preview", async () => {
    renderDashboard(root, { sessions, scenarios, personas, macros });
    const button = root.querySelector<HTMLButtonElement>('[data-testid="macro-repair-preview-login"]');
    expect(button).not.toBeNull();

    button?.click();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Repair preview: login");
    expect(dialog?.textContent).toContain("Click by text");
    expect(dialog?.textContent).toContain("Review selector '#submit'");
    expect(dialog?.querySelector("button.btn--primary")).toBeNull();
  });
  it("preserves collapsible macro panel state across refresh renders", () => {
    renderDashboard(root, { sessions, scenarios, personas, macros });
    const first = root.querySelector<HTMLDetailsElement>('[data-testid="panel-macros"]');
    expect(first).not.toBeNull();
    if (first) first.open = true;

    renderDashboard(root, { sessions, scenarios, personas, macros });

    expect(root.querySelector<HTMLDetailsElement>('[data-testid="panel-macros"]')?.open).toBe(true);
  });
});
