import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  renderMacroActionSummaryList,
  renderMacroRepairPreview,
  renderMacroSelectorTools,
  renderMacroSummary,
} from "./macro-renderers.js";
import type { MacroDetail, MacroRepairPreview, SessionListResponse } from "./types.js";

const apiMocks = vi.hoisted(() => ({
  validateSessionSelector: vi.fn(),
}));

vi.mock("./api.js", () => apiMocks);

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = "";
});

const sessions: SessionListResponse = {
  live: [
    {
      id: "live-1",
      kind: "chromium",
      label: null,
      profile: null,
      url: null,
      started_at: "2026-05-01T00:00:00Z",
      live: true,
      log_path: "live.jsonl",
    },
  ],
  closed: [
    {
      id: "closed-1",
      kind: "firefox",
      label: null,
      profile: null,
      url: null,
      started_at: "2026-05-01T00:00:00Z",
      live: false,
      log_path: "closed.jsonl",
    },
  ],
};

function flushPromises(): Promise<void> {
  return Promise.resolve().then(() => Promise.resolve()).then(() => Promise.resolve());
}

describe("macro summary renderers", () => {
  it("handles non-array and empty action lists", () => {
    const detail = { name: "bad", actions: "not-array" } as unknown as MacroDetail;
    const summary = renderMacroSummary(detail);
    expect(summary.textContent).toContain("0 action(s)");
    expect(summary.textContent).toContain("No actions");
    expect(renderMacroActionSummaryList([]).textContent).toContain("No actions");
  });

  it("renders conditional, try-each, selector, and unknown action edge cases", () => {
    const summary = renderMacroActionSummaryList([
      { action: "if_selector", selector: undefined, then: undefined, else: undefined },
      { action: "try_each", branches: undefined },
      { action: "try_each", branches: [[{ action: "click", selector: "#go" }], undefined as never] },
      { action: "", selector: "#fallback" },
    ]);

    expect(summary.textContent).toContain("if_selector");
    expect(summary.textContent).toContain("0 then");
    expect(summary.textContent).toContain("try_each (0 branches)");
    expect(summary.textContent).toContain("branch 2 (0 actions)");
    expect(summary.textContent).toContain("(unknown action)");
    expect(summary.textContent).toContain("#fallback");
  });
});

describe("renderMacroSelectorTools", () => {
  it("shows an empty state without sessions", () => {
    const tools = renderMacroSelectorTools({ live: [], closed: [] }, () => {});
    expect(tools.textContent).toContain("No sessions available");
  });

  it("requires a selector before validating", () => {
    const setError = vi.fn();
    const tools = renderMacroSelectorTools(sessions, setError);
    tools.querySelector<HTMLButtonElement>("button")?.click();
    expect(setError).toHaveBeenCalledWith("Enter a selector to validate.");
  });

  it("shows success and not-found validation statuses", async () => {
    apiMocks.validateSessionSelector
      .mockResolvedValueOnce({ ok: true, selector: "#one", found: true, count: 1 })
      .mockResolvedValueOnce({ ok: true, selector: "#many", found: true, count: 2 })
      .mockResolvedValueOnce({ ok: true, selector: "#none", found: false, count: 0 });
    const tools = renderMacroSelectorTools(sessions, () => {});
    const input = tools.querySelector<HTMLInputElement>("input")!;
    const button = tools.querySelector<HTMLButtonElement>("button")!;
    const status = tools.querySelector<HTMLElement>(".macro-selector-tools__status")!;

    input.value = "#one";
    button.click();
    await flushPromises();
    expect(status.textContent).toContain("found (1 match)");

    input.value = "#many";
    button.click();
    await flushPromises();
    expect(status.textContent).toContain("found (2 matches)");

    input.value = "#none";
    button.click();
    await flushPromises();
    expect(status.textContent).toContain("not found");
  });

  it("shows validation failures and restores the button", async () => {
    apiMocks.validateSessionSelector.mockRejectedValueOnce(new Error("offline"));
    const tools = renderMacroSelectorTools(sessions, () => {});
    const input = tools.querySelector<HTMLInputElement>("input")!;
    const button = tools.querySelector<HTMLButtonElement>("button")!;
    const status = tools.querySelector<HTMLElement>(".macro-selector-tools__status")!;

    input.value = "#bad";
    button.click();
    expect(button.disabled).toBe(true);
    await flushPromises();

    expect(status.textContent).toContain("Validation failed");
    expect(status.classList.contains("macro-selector-tools__status--error")).toBe(true);
    expect(button.disabled).toBe(false);
  });
});

describe("renderMacroRepairPreview", () => {
  it("renders suggestions without optional preview or replacement blocks", () => {
    const preview: MacroRepairPreview = {
      macro: "login",
      suggestions: [
        {
          macro: "login",
          action_index: 2,
          original_action: { action: "click" },
          source: "test",
          replacement_action: null,
          action_preview: null,
          prompt: "Check it",
        },
      ],
    };

    const el = renderMacroRepairPreview(preview);
    expect(el.textContent).toContain("Action 2");
    expect(el.querySelector("pre")).toBeNull();
    expect(el.querySelector(".repair-preview__action")).toBeNull();
  });
});
