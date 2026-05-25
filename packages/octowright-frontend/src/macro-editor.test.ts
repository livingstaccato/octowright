import { beforeEach, describe, expect, it, vi } from "vitest";
import { openMacroEditor, openMacroRepairPreview } from "./macro-editor.js";

const apiMocks = vi.hoisted(() => ({
  getMacro: vi.fn(),
  getMacroRepairPreview: vi.fn(),
  updateMacro: vi.fn(),
  validateMacro: vi.fn(),
  validateSessionSelector: vi.fn(),
}));

vi.mock("./api.js", () => apiMocks);

const detail = {
  name: "login",
  description: null,
  parameters: [],
  created_at: null,
  updated_at: null,
  actions: [{ action: "click", selector: "#go" }],
};

const sessions = { live: [], closed: [] };

async function flushPromises(): Promise<void> {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}

beforeEach(() => {
  document.body.innerHTML = "";
  vi.clearAllMocks();
  apiMocks.getMacro.mockResolvedValue(detail);
  apiMocks.getMacroRepairPreview.mockResolvedValue({ macro: "login", suggestions: [] });
  apiMocks.validateMacro.mockResolvedValue({ ok: true, valid: true, issues: [] });
  apiMocks.updateMacro.mockResolvedValue({ ok: true, name: "login" });
});

describe("openMacroEditor edge behavior", () => {
  it("removes an existing modal before opening a new editor", () => {
    openMacroEditor("login", sessions, vi.fn());
    openMacroEditor("login", sessions, vi.fn());
    expect(document.querySelectorAll(".modal-backdrop").length).toBe(1);
  });

  it("closes when the editor backdrop itself is clicked", async () => {
    openMacroEditor("login", sessions, vi.fn());
    await flushPromises();

    document.querySelector<HTMLElement>(".modal-backdrop")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(document.querySelector(".modal-backdrop")).toBeNull();
  });

  it("shows an inline error for invalid JSON before saving", async () => {
    openMacroEditor("login", sessions, vi.fn());
    await flushPromises();
    const textarea = document.querySelector<HTMLTextAreaElement>("textarea.yaml-editor")!;
    textarea.value = "{";

    document.querySelector<HTMLButtonElement>('[data-testid="macro-save-login"]')?.click();

    expect(document.querySelector('[data-testid="macro-editor-error-login"]')?.textContent).toContain(
      "Macro JSON is invalid",
    );
    expect(apiMocks.validateMacro).not.toHaveBeenCalled();
  });

  it("shows validation issues and re-enables save", async () => {
    apiMocks.validateMacro.mockResolvedValueOnce({
      ok: false,
      valid: false,
      issues: [{ code: "bad_action", message: "Unknown action", severity: "error" }],
    });
    openMacroEditor("login", sessions, vi.fn());
    await flushPromises();

    const save = document.querySelector<HTMLButtonElement>('[data-testid="macro-save-login"]')!;
    save.click();
    await flushPromises();

    expect(document.querySelector('[data-testid="macro-editor-error-login"]')?.textContent).toContain(
      "bad_action: Unknown action",
    );
    expect(save.disabled).toBe(false);
    expect(save.textContent).toBe("Save");
  });
});

describe("openMacroRepairPreview edge behavior", () => {
  it("removes an existing modal before opening a repair preview", () => {
    openMacroRepairPreview("login");
    openMacroRepairPreview("login");
    expect(document.querySelectorAll(".modal-backdrop").length).toBe(1);
  });

  it("closes when the repair-preview backdrop itself is clicked", async () => {
    openMacroRepairPreview("login");
    await flushPromises();

    document.querySelector<HTMLElement>(".modal-backdrop")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(document.querySelector(".modal-backdrop")).toBeNull();
  });
});
