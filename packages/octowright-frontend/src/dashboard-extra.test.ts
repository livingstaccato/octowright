// Coverage for dashboard.ts: openPersonaEditor, formatBytes, snackbar timer,
// deleteSessionRecording, relaunchClosedSession, startSavedScenario, persona
// edit button, saved-scenario sort/start, and openMacroEditor error paths.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  PersonaSummary,
  SavedScenario,
  ScenarioListResponse,
  SessionListResponse,
} from "./types.js";

const apiMocks = vi.hoisted(() => ({
  dashboardEventsUrl: vi.fn(() => "/api/dashboard/events"),
  deleteRecording: vi.fn(),
  getMacro: vi.fn(async () => ({ name: "m", description: "", parameters: [], created_at: "", updated_at: "", actions: [] })),
  getMacroRepairPreview: vi.fn(),
  getMacros: vi.fn(async () => []),
  getPersonaDetail: vi.fn(),
  getPersonas: vi.fn(async () => []),
  getPersonaSizes: vi.fn(async () => ({})),
  getScenarios: vi.fn(async () => ({ live: [] })),
  getSessions: vi.fn(async () => ({ live: [], closed: [] })),
  relaunchSession: vi.fn(),
  startScenario: vi.fn(),
  updateMacro: vi.fn(async () => ({ ok: true, name: "m" })),
  updatePersonaYaml: vi.fn(),
  validateMacro: vi.fn(async () => ({ ok: true, valid: true, issues: [] })),
  validateSessionSelector: vi.fn(),
}));

vi.mock("./api.js", () => apiMocks);

// Import the module after mocks are registered.
const dashboard = await import("./dashboard.js");
const { formatBytes, openPersonaEditor, openMacroRepairPreview, renderDashboard, showSnackbar } = dashboard;

const emptySessions: SessionListResponse = { live: [], closed: [] };
const emptyScenarios: ScenarioListResponse = { live: [] };

const closedSession = {
  id: "C1",
  kind: "firefox" as const,
  label: null,
  profile: "default",
  url: null,
  started_at: "2026-04-23T08:00:00Z",
  live: false,
  log_path: "y",
};

const personaFixture: PersonaSummary = {
  name: "shopper",
  display_name: "Shopper",
  engines: ["chromium"],
  last_used: "2026-04-24T12:00:00Z",
};

const savedScenarioA: SavedScenario = { name: "alpha", path: "/a", form: "yaml", mtime: 1000 };
const savedScenarioB: SavedScenario = { name: "beta", path: "/b", form: "python", mtime: 2000 };

async function flushMicrotasks(): Promise<void> {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}

let root: HTMLDivElement;

// snackbarEl is a module-level singleton. Avoid document.body.innerHTML = ""
// so the cached element stays attached. Remove only non-snackbar children.
function clearBody(): void {
  for (const child of Array.from(document.body.children)) {
    if (!child.classList.contains("snackbar")) child.remove();
  }
  document.querySelector(".modal-backdrop")?.remove();
}

beforeEach(() => {
  vi.useFakeTimers();
  clearBody();
  apiMocks.getPersonaDetail.mockReset();
  apiMocks.deleteRecording.mockReset();
  apiMocks.relaunchSession.mockReset();
  apiMocks.startScenario.mockReset();
  apiMocks.updatePersonaYaml.mockReset();
  apiMocks.getMacroRepairPreview.mockReset();
  apiMocks.getSessions.mockReset().mockResolvedValue(emptySessions);
  apiMocks.getScenarios.mockReset().mockResolvedValue(emptyScenarios);
  apiMocks.getPersonas.mockReset().mockResolvedValue([]);
  apiMocks.getMacros.mockReset().mockResolvedValue([]);
  root = document.createElement("div");
  document.body.append(root);
});

afterEach(() => {
  dashboard.disposeDashboard();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("formatBytes", () => {
  it("formats bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
  });

  it("formats kilobytes", () => {
    expect(formatBytes(1024)).toBe("1 KB");
    expect(formatBytes(2048)).toBe("2 KB");
  });

  it("formats megabytes", () => {
    expect(formatBytes(1_048_576)).toBe("1.0 MB");
    expect(formatBytes(5_242_880)).toBe("5.0 MB");
  });

  it("formats gigabytes", () => {
    expect(formatBytes(1_073_741_824)).toBe("1.0 GB");
    expect(formatBytes(2_147_483_648)).toBe("2.0 GB");
  });
});

describe("showSnackbar", () => {
  it("shows a message and hides it after 3.5 s", () => {
    showSnackbar("hello");
    // After the first call the snackbar element is created and appended.
    const el = document.body.querySelector(".snackbar");
    expect(el).not.toBeNull();
    expect(el?.textContent).toBe("hello");
    expect(el?.className).not.toContain("snackbar--hidden");

    vi.advanceTimersByTime(3500);
    expect(el?.className).toContain("snackbar--hidden");
  });

  it("shows an error snackbar with the error class", () => {
    showSnackbar("oh no", true);
    const el = document.body.querySelector(".snackbar");
    expect(el?.className).toContain("snackbar--error");
  });

  it("resets the hide timer when called a second time", () => {
    showSnackbar("first");
    vi.advanceTimersByTime(2000);
    showSnackbar("second");
    // At t=3500 from the first call the element should still be visible
    // because the second call reset the 3.5-second timer.
    vi.advanceTimersByTime(1500);
    const el = document.body.querySelector(".snackbar");
    expect(el?.className).not.toContain("snackbar--hidden");
  });
});

describe("openPersonaEditor", () => {
  const personaDetail = {
    name: "shopper",
    yaml: "name: shopper\nurl: https://shop.example\n",
    path: "/profiles/shopper",
    disk_bytes: 2_097_152,
    engine_bytes: { chromium: 1_048_576, firefox: 1_048_576 },
  };

  it("opens a modal with disk info and a textarea", async () => {
    apiMocks.getPersonaDetail.mockResolvedValueOnce(personaDetail);

    openPersonaEditor("shopper");
    await flushMicrotasks();

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.getAttribute("aria-label")).toBe("Edit persona: shopper");
    expect(dialog?.textContent).toContain("Edit persona: shopper");
    expect(dialog?.textContent).toContain("2.0 MB");
    expect(dialog?.querySelector("textarea.yaml-editor")).not.toBeNull();
  });

  it("shows engine-level disk breakdown", async () => {
    apiMocks.getPersonaDetail.mockResolvedValueOnce(personaDetail);

    openPersonaEditor("shopper");
    await flushMicrotasks();

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("chromium");
    expect(dialog?.textContent).toContain("firefox");
  });

  it("shows error message when getPersonaDetail fails", async () => {
    apiMocks.getPersonaDetail.mockRejectedValueOnce(new Error("not found"));

    openPersonaEditor("shopper");
    await flushMicrotasks();

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Failed to load persona");
    expect(dialog?.textContent).toContain("not found");
  });

  it("calls updatePersonaYaml and closes modal on save success", async () => {
    apiMocks.getPersonaDetail.mockResolvedValueOnce(personaDetail);
    apiMocks.updatePersonaYaml.mockResolvedValueOnce({});

    openPersonaEditor("shopper");
    await flushMicrotasks();

    const saveBtn = document.querySelector<HTMLButtonElement>(".modal__footer .btn--primary");
    expect(saveBtn).not.toBeNull();
    saveBtn?.click();
    await flushMicrotasks();

    expect(apiMocks.updatePersonaYaml).toHaveBeenCalledWith("shopper", personaDetail.yaml);
    expect(document.querySelector(".modal-backdrop")).toBeNull();
  });

  it("shows error snackbar and re-enables save button when updatePersonaYaml fails", async () => {
    apiMocks.getPersonaDetail.mockResolvedValueOnce(personaDetail);
    apiMocks.updatePersonaYaml.mockRejectedValueOnce(new Error("server error"));

    openPersonaEditor("shopper");
    await flushMicrotasks();

    const saveBtn = document.querySelector<HTMLButtonElement>(".modal__footer .btn--primary");
    saveBtn?.click();
    await flushMicrotasks();

    expect(document.body.querySelector(".snackbar")?.textContent).toContain("Save failed");
    expect(saveBtn?.disabled).toBe(false);
    expect(saveBtn?.textContent).toBe("Save");
  });

  it("closes modal when cancel button is clicked", async () => {
    apiMocks.getPersonaDetail.mockResolvedValueOnce(personaDetail);

    openPersonaEditor("shopper");
    await flushMicrotasks();

    const cancelBtn = document.querySelector<HTMLButtonElement>(".modal__footer .btn:not(.btn--primary)");
    cancelBtn?.click();

    expect(document.querySelector(".modal-backdrop")).toBeNull();
  });

  it("removes existing modal before opening a new one", async () => {
    apiMocks.getPersonaDetail.mockResolvedValue(personaDetail);

    openPersonaEditor("shopper");
    openPersonaEditor("shopper");
    await flushMicrotasks();

    expect(document.querySelectorAll(".modal-backdrop").length).toBe(1);
  });
});

describe("openMacroRepairPreview error path", () => {
  it("shows error message when getMacroRepairPreview fails", async () => {
    apiMocks.getMacroRepairPreview.mockRejectedValueOnce(new Error("network failure"));

    openMacroRepairPreview("login");
    await flushMicrotasks();

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Failed to load repair preview");
    expect(dialog?.textContent).toContain("network failure");
  });

  it("shows 'no suggestions' when suggestion list is empty", async () => {
    apiMocks.getMacroRepairPreview.mockResolvedValueOnce({ macro: "login", suggestions: [] });

    openMacroRepairPreview("login");
    await flushMicrotasks();

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("No selector-based repair suggestions");
  });

});

describe("closed session row actions", () => {
  const stateWithClosed = {
    sessions: { live: [], closed: [closedSession] },
    scenarios: emptyScenarios,
    personas: [],
    macros: [],
  };

  it("delete button calls deleteRecording and refreshes dashboard", async () => {
    apiMocks.deleteRecording.mockResolvedValueOnce({ deleted: true, session_id: "C1", files_removed: 3 });

    renderDashboard(root, stateWithClosed);
    const delBtn = root.querySelector<HTMLButtonElement>('[aria-label="Delete recording"]');
    expect(delBtn).not.toBeNull();
    delBtn?.click();
    await flushMicrotasks();

    expect(apiMocks.deleteRecording).toHaveBeenCalledWith("C1");
    expect(document.body.querySelector(".snackbar")?.textContent).toContain("Deleted session");
  });

  it("delete button shows error snackbar when deleteRecording fails", async () => {
    apiMocks.deleteRecording.mockRejectedValueOnce(new Error("disk error"));

    renderDashboard(root, stateWithClosed);
    const delBtn = root.querySelector<HTMLButtonElement>('[aria-label="Delete recording"]');
    delBtn?.click();
    await flushMicrotasks();

    expect(document.body.querySelector(".snackbar")?.textContent).toContain("Delete failed");
  });

  it("relaunch button calls relaunchSession and refreshes dashboard", async () => {
    apiMocks.relaunchSession.mockResolvedValueOnce({
      id: "R1_new",
      kind: "firefox",
      label: null,
      profile: "default",
      url: null,
      started_at: "2026-05-01T00:00:00Z",
      live: true,
      log_path: "r.jsonl",
    });

    renderDashboard(root, stateWithClosed);
    const relaunchBtn = root.querySelector<HTMLButtonElement>('[aria-label="Relaunch with same params"]');
    expect(relaunchBtn).not.toBeNull();
    relaunchBtn?.click();
    await flushMicrotasks();

    expect(apiMocks.relaunchSession).toHaveBeenCalledWith("C1");
    expect(document.body.querySelector(".snackbar")?.textContent).toContain("Relaunched");
  });

  it("relaunch button shows error snackbar when relaunchSession fails", async () => {
    apiMocks.relaunchSession.mockRejectedValueOnce(new Error("already live"));

    renderDashboard(root, stateWithClosed);
    const relaunchBtn = root.querySelector<HTMLButtonElement>('[aria-label="Relaunch with same params"]');
    relaunchBtn?.click();
    await flushMicrotasks();

    expect(document.body.querySelector(".snackbar")?.textContent).toContain("Relaunch failed");
  });
});

describe("saved scenario start button", () => {
  const stateWithSaved = {
    sessions: emptySessions,
    scenarios: { live: [], saved: [savedScenarioA, savedScenarioB] },
    personas: [],
    macros: [],
  };

  it("sorts saved scenarios by mtime descending", () => {
    renderDashboard(root, stateWithSaved);
    const items = root.querySelectorAll('[data-testid="panel-saved-scenarios"] .scenario-list__item');
    expect(items.length).toBe(2);
    // beta has mtime 2000 (newer) so should appear first
    expect(items[0]?.getAttribute("data-scenario-name")).toBe("beta");
  });

  it("start button calls startScenario and refreshes", async () => {
    apiMocks.startScenario.mockResolvedValueOnce({
      scenario_id: "sc-1",
      name: "alpha",
      participants: [{ role: "player", persona: "shopper", kind: "chromium", instance_id: "I1" }],
    });

    renderDashboard(root, stateWithSaved);
    const startBtn = root.querySelector<HTMLButtonElement>('[aria-label="Start scenario alpha"]');
    expect(startBtn).not.toBeNull();
    startBtn?.click();
    await flushMicrotasks();

    expect(apiMocks.startScenario).toHaveBeenCalledWith("alpha");
    expect(document.body.querySelector(".snackbar")?.textContent).toContain("Started 'alpha'");
  });

  it("start button shows error snackbar when startScenario fails", async () => {
    apiMocks.startScenario.mockRejectedValueOnce(new Error("conflict"));

    renderDashboard(root, stateWithSaved);
    const startBtn = root.querySelector<HTMLButtonElement>('[aria-label="Start scenario alpha"]');
    startBtn?.click();
    await flushMicrotasks();

    expect(document.body.querySelector(".snackbar")?.textContent).toContain("Start failed");
  });
});

describe("persona grid edit button", () => {
  const personaDetail = {
    name: "shopper",
    yaml: "name: shopper\n",
    path: "/profiles/shopper",
    disk_bytes: 0,
    engine_bytes: {},
  };

  it("opens persona editor when edit button is clicked", async () => {
    apiMocks.getPersonaDetail.mockResolvedValueOnce(personaDetail);

    renderDashboard(root, {
      sessions: emptySessions,
      scenarios: emptyScenarios,
      personas: [personaFixture],
      macros: [],
    });

    const editBtn = root.querySelector<HTMLButtonElement>('[aria-label="Edit persona shopper"]');
    expect(editBtn).not.toBeNull();
    editBtn?.click();
    await flushMicrotasks();

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.getAttribute("aria-label")).toBe("Edit persona: shopper");
  });
});

describe("loadPersonaSizes error path", () => {
  it("silently handles getPersonaSizes rejection", async () => {
    apiMocks.getPersonaSizes.mockRejectedValueOnce(new Error("timeout"));
    vi.stubGlobal("EventSource", undefined);
    await expect(dashboard.bootDashboard(root)).resolves.toBeTypeOf("function");
    await flushMicrotasks();
  });
});

describe("openMacroEditor error paths and refreshDashboard", () => {
  const macroFixture = { name: "login", description: "", parameters: [], updated_at: "2026-04-23T00:00:00Z" };
  const macroDetail = { name: "login", description: "", parameters: [], created_at: "", updated_at: "", actions: [] };

  it("shows 'Save failed' when validation succeeds but updateMacro rejects", async () => {
    apiMocks.getMacro.mockResolvedValueOnce(macroDetail);
    apiMocks.validateMacro.mockResolvedValueOnce({ ok: true, valid: true, issues: [] });
    apiMocks.updateMacro.mockRejectedValueOnce(new Error("write error"));

    renderDashboard(root, { sessions: emptySessions, scenarios: emptyScenarios, personas: [], macros: [macroFixture] });
    root.querySelector<HTMLButtonElement>(`[data-testid="macro-edit-login"]`)?.click();
    await flushMicrotasks();

    const saveBtn = document.querySelector<HTMLButtonElement>('[data-testid="macro-save-login"]');
    saveBtn?.click();
    await flushMicrotasks();

    const errorEl = document.querySelector('[data-testid="macro-editor-error-login"]');
    expect(errorEl?.textContent).toContain("Save failed");
    expect(errorEl?.textContent).toContain("write error");
  });

  it("shows 'Macro JSON is invalid' when textarea has bad JSON", async () => {
    apiMocks.getMacro.mockResolvedValueOnce(macroDetail);

    renderDashboard(root, { sessions: emptySessions, scenarios: emptyScenarios, personas: [], macros: [macroFixture] });
    root.querySelector<HTMLButtonElement>(`[data-testid="macro-edit-login"]`)?.click();
    await flushMicrotasks();

    const textarea = document.querySelector<HTMLTextAreaElement>(".modal__body textarea");
    if (textarea) textarea.value = "{ invalid json";

    const saveBtn = document.querySelector<HTMLButtonElement>('[data-testid="macro-save-login"]');
    saveBtn?.click();
    await flushMicrotasks();

    const errorEl = document.querySelector('[data-testid="macro-editor-error-login"]');
    expect(errorEl?.textContent).toContain("Macro JSON is invalid");
  });

  it("shows validation failure message when validateMacro returns invalid", async () => {
    apiMocks.getMacro.mockResolvedValueOnce(macroDetail);
    apiMocks.validateMacro.mockResolvedValueOnce({
      ok: true,
      valid: false,
      issues: [{ code: "E001", message: "bad action" }],
    });

    renderDashboard(root, { sessions: emptySessions, scenarios: emptyScenarios, personas: [], macros: [macroFixture] });
    root.querySelector<HTMLButtonElement>(`[data-testid="macro-edit-login"]`)?.click();
    await flushMicrotasks();

    document.querySelector<HTMLButtonElement>('[data-testid="macro-save-login"]')?.click();
    await flushMicrotasks();

    const errorEl = document.querySelector('[data-testid="macro-editor-error-login"]');
    expect(errorEl?.textContent).toContain("E001");
    expect(errorEl?.textContent).toContain("bad action");
  });

  it("calls refreshDashboard after a successful macro save", async () => {
    apiMocks.getMacro.mockResolvedValueOnce(macroDetail);
    apiMocks.validateMacro.mockResolvedValueOnce({ ok: true, valid: true, issues: [] });
    apiMocks.updateMacro.mockResolvedValueOnce({ ok: true, name: "login" });
    vi.stubGlobal("EventSource", undefined);
    await dashboard.bootDashboard(root);
    await flushMicrotasks();

    renderDashboard(root, { sessions: emptySessions, scenarios: emptyScenarios, personas: [], macros: [macroFixture] });
    root.querySelector<HTMLButtonElement>(`[data-testid="macro-edit-login"]`)?.click();
    await flushMicrotasks();

    document.querySelector<HTMLButtonElement>('[data-testid="macro-save-login"]')?.click();
    await flushMicrotasks();
    expect(document.querySelector(".modal-backdrop")).toBeNull();
  });
});
