import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadState, renderDashboard } from "./dashboard.js";
import * as api from "./api.js";
import type {
  DemoListResponse,
  LiveScenario,
  MacroSummary,
  PersonaSummary,
  ScenarioListResponse,
  SessionListResponse,
} from "./types.js";

const macroDetail = JSON.parse(
  `{"name":"login","description":"logs in","parameters":["user"],"created_at":"2026-04-23T00:00:00Z","updated_at":"2026-04-23T00:00:00Z","actions":[{"action":"if_selector","selector":".cookie-banner","then":[{"action":"click","selector":".accept"}],"else":[{"action":"click","selector":".dismiss"}]},{"action":"try_each","branches":[[{"action":"fill","selector":"#x"}],[{"action":"fill_by","selector":"#y"},{"action":"click","selector":"#go"}]]}]}`,
);

vi.mock("./api.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api.js")>();
  return {
    ...actual,
    getMacro: vi.fn(async () => macroDetail),
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
    validateMacro: vi.fn(async () => ({ ok: true, valid: true, issues: [] })),
    updateMacro: vi.fn(async () => ({ ok: true, name: "login" })),
    validateSessionSelector: vi.fn(async () => ({ ok: true, present: true, selector: "#x", session_id: "L1" })),
    getSessions: vi.fn(async () => ({ live: [], closed: [] })),
    getScenarios: vi.fn(async () => ({ live: [] })),
    getPersonas: vi.fn(async () => []),
    getMacros: vi.fn(async () => []),
    getDemos: vi.fn(async () => ({ heroes: [], supporting: [] })),
  };
});

let root: HTMLDivElement;
beforeEach(() => {
  document.body.innerHTML = "";
  vi.clearAllMocks();
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

const demos: DemoListResponse = {
  heroes: [
    {
      id: "hero-checkout",
      title: "Checkout Hero",
      summary: "Walk through a complete checkout flow.",
      hero: true,
      audiences: ["sales"],
      tags: ["checkout"],
      engines: ["chromium"],
      roles: ["shopper"],
      scenarios: ["checkout-two-party"],
      regen_command: "uv run octowright demo regen hero-checkout",
      tutorial_export: null,
      artifacts: {
        replay: {
          declared_count: 1,
          existing_count: 1,
          declared_paths: ["replays/checkout.jsonl"],
          existing_paths: ["replays/checkout.jsonl"],
        },
        video: {
          declared_count: 1,
          existing_count: 1,
          declared_paths: ["videos/checkout.mp4"],
          existing_paths: ["videos/checkout.mp4"],
        },
      },
    },
  ],
  supporting: [
    {
      id: "support-admin",
      title: "Admin Support",
      summary: "Covers the supporting admin workflow.",
      hero: false,
      audiences: ["ops"],
      tags: ["admin"],
      engines: ["firefox"],
      roles: ["admin"],
      scenarios: ["admin-backoffice"],
      regen_command: null,
      tutorial_export: null,
      artifacts: {
        replay: {
          declared_count: 0,
          existing_count: 0,
          declared_paths: [],
          existing_paths: [],
        },
        video: {
          declared_count: 1,
          existing_count: 0,
          declared_paths: ["videos/admin.mp4"],
          existing_paths: [],
        },
      },
    },
  ],
};

describe("renderDashboard", () => {
  it("renders all five panels", () => {
    renderDashboard(root, { sessions, scenarios, personas, macros, demos });
    expect(root.querySelector('[data-testid="panel-demo-gallery"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="panel-live-browsers"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="panel-live-scenarios"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="panel-personas"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="panel-closed-sessions"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="panel-macros"]')).not.toBeNull();
  });
  it("renders live and closed session rows", () => {
    renderDashboard(root, { sessions, scenarios, personas, macros, demos });
    const live = root.querySelector('[data-testid="table-live-sessions"]');
    expect(live?.querySelectorAll("tbody tr").length).toBe(1);
    const closed = root.querySelector('[data-testid="table-closed-sessions"]');
    expect(closed?.querySelectorAll("tbody tr").length).toBe(1);
  });
  it("renders scenario chips with links", () => {
    renderDashboard(root, { sessions, scenarios, personas, macros, demos });
    const chips = root.querySelectorAll('[data-testid="panel-live-scenarios"] a.chip');
    expect(chips.length).toBe(2);
    expect((chips[0] as HTMLAnchorElement).href).toContain("/sessions/L1");
  });
  it("renders the demo gallery within the dashboard", () => {
    renderDashboard(root, { sessions, scenarios, personas, macros, demos });
    const gallery = root.querySelector('[data-testid="panel-demo-gallery"]');
    expect(gallery?.querySelector(".scenario-list")).not.toBeNull();
    expect(gallery?.querySelector('[data-testid="demo-heroes"] li[data-demo-id="hero-checkout"]')).not.toBeNull();
    expect(gallery?.querySelector('[data-testid="demo-supporting"] li[data-demo-id="support-admin"]')).not.toBeNull();
    expect(gallery?.textContent).toContain("Checkout Hero");
    expect(gallery?.textContent).toContain("Admin Support");
    expect(gallery?.textContent).toContain("Replay artifacts: 1/1");
    expect(gallery?.querySelector("code")?.textContent).toBe("uv run octowright demo regen hero-checkout");
  });
  it("shows empty placeholders when nothing is provided", () => {
    renderDashboard(root, {
      sessions: { live: [], closed: [] },
      scenarios: { live: [] },
      personas: [],
      macros: [],
      demos: { heroes: [], supporting: [] },
    });
    const empties = root.querySelectorAll(".empty");
    expect(empties.length).toBeGreaterThanOrEqual(4);
  });
  it("opens a non-mutating macro repair preview", async () => {
    renderDashboard(root, { sessions, scenarios, personas, macros, demos });
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
  it("opens macro editor and shows conditional summaries", async () => {
    renderDashboard(root, { sessions, scenarios, personas, macros, demos });
    const button = root.querySelector<HTMLButtonElement>('[data-testid="macro-edit-login"]');
    expect(button).not.toBeNull();
    button?.click();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain("Macro structure summary");
    expect(dialog?.textContent).toContain("if_selector .cookie-banner");
    expect(dialog?.textContent).toContain("try_each (2 branches)");
    expect(dialog?.textContent).toContain("branch 1");
    expect(dialog?.textContent).toContain("branch 2");
  });
  it("validates and saves macro from editor", async () => {
    const validateSpy = vi.mocked(api.validateMacro);
    const updateSpy = vi.mocked(api.updateMacro);
    renderDashboard(root, { sessions, scenarios, personas, macros, demos });
    const button = root.querySelector<HTMLButtonElement>('[data-testid="macro-edit-login"]');
    button?.click();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const saveBtn = document.querySelector<HTMLButtonElement>('[data-testid="macro-save-login"]');
    const textarea = document.querySelector<HTMLTextAreaElement>(".modal__body textarea");
    textarea.value = JSON.stringify({ ...macroDetail });
    saveBtn?.click();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(validateSpy).toHaveBeenCalledWith("login", expect.any(Object));
    expect(updateSpy).toHaveBeenCalledTimes(1);
  });
  it("validates selector against active session", async () => {
    const validateSessionSpy = vi.mocked(api.validateSessionSelector);
    renderDashboard(root, { sessions, scenarios, personas, macros, demos });
    const button = root.querySelector<HTMLButtonElement>('[data-testid="macro-edit-login"]');
    button?.click();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const selectorInput = document.querySelector<HTMLInputElement>(".macro-selector-tools__selector");
    selectorInput.value = "#test";
    const validateBtn = document.querySelector<HTMLButtonElement>(".macro-selector-tools .btn");
    validateBtn?.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(validateSessionSpy).toHaveBeenCalledWith("L1", "#test");
  });
  it("preserves collapsible macro panel state across refresh renders", () => {
    renderDashboard(root, { sessions, scenarios, personas, macros, demos });
    const first = root.querySelector<HTMLDetailsElement>('[data-testid="panel-macros"]');
    expect(first).not.toBeNull();
    if (first) first.open = true;

    renderDashboard(root, { sessions, scenarios, personas, macros, demos });

    expect(root.querySelector<HTMLDetailsElement>('[data-testid="panel-macros"]')?.open).toBe(true);
  });
});

describe("loadState", () => {
  it("includes demos from getDemos", async () => {
    vi.mocked(api.getSessions).mockResolvedValueOnce(sessions);
    vi.mocked(api.getScenarios).mockResolvedValueOnce(scenarios);
    vi.mocked(api.getPersonas).mockResolvedValueOnce(personas);
    vi.mocked(api.getMacros).mockResolvedValueOnce(macros);
    vi.mocked(api.getDemos).mockResolvedValueOnce(demos);

    const state = await loadState();

    expect(state.demos).toEqual(demos);
    expect(api.getDemos).toHaveBeenCalledTimes(1);
  });

  it("falls back to empty demos when getDemos fails", async () => {
    vi.mocked(api.getSessions).mockResolvedValueOnce(sessions);
    vi.mocked(api.getScenarios).mockResolvedValueOnce(scenarios);
    vi.mocked(api.getPersonas).mockResolvedValueOnce(personas);
    vi.mocked(api.getMacros).mockResolvedValueOnce(macros);
    vi.mocked(api.getDemos).mockRejectedValueOnce(new Error("boom"));

    const state = await loadState();

    expect(state.demos).toEqual({ heroes: [], supporting: [] });
  });
});
