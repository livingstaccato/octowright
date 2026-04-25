import { beforeEach, describe, expect, it } from "vitest";
import { renderDashboard } from "./dashboard.js";
import type { LiveScenario, MacroSummary, PersonaSummary, SessionListResponse, ScenarioListResponse } from "./types.js";

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
});
