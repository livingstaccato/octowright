// Macro detail/repair renderers shared by the macro editor + repair-preview
// modals and the macro list. Pure DOM construction; no module-level state.

import { validateSessionSelector } from "./api.js";
import type { MacroAction, MacroDetail, MacroRepairPreview, SessionListResponse } from "./types.js";

export function renderMacroSummary(detail: MacroDetail): HTMLElement {
  const container = document.createElement("div");
  container.className = "macro-summary";
  const actions = Array.isArray(detail.actions) ? detail.actions : [];
  const count = document.createElement("div");
  count.className = "macro-summary__count";
  count.textContent = `${actions.length} action(s)`;
  const summaryList = renderMacroActionSummaryList(actions);
  container.append(count, summaryList);
  return container;
}

export function renderMacroActionSummaryList(actions: MacroAction[]): HTMLElement {
  const ul = document.createElement("ul");
  ul.className = "macro-summary__list";
  if (!Array.isArray(actions) || actions.length === 0) {
    const empty = document.createElement("li");
    empty.className = "macro-summary__item macro-summary__item--empty";
    empty.textContent = "No actions";
    ul.append(empty);
    return ul;
  }
  for (const action of actions) {
    ul.append(renderMacroActionSummaryItem(action));
  }
  return ul;
}

function renderMacroActionSummaryItem(action: MacroAction): HTMLLIElement {
  const li = document.createElement("li");
  li.className = "macro-summary__item";
  const title = document.createElement("div");
  title.className = "macro-summary__action";

  if (action.action === "if_selector") {
    title.textContent = `if_selector ${String(action.selector ?? "∅")}`;
    const meta = document.createElement("span");
    meta.className = "macro-summary__meta";
    const thenCount = Array.isArray(action.then) ? action.then.length : 0;
    const elseCount = Array.isArray(action.else) ? action.else.length : 0;
    meta.textContent = `${thenCount} then · ${elseCount} else`;
    title.append(" ");
    title.append(meta);
    li.append(title);

    const thenWrap = document.createElement("ul");
    thenWrap.className = "macro-summary__branch";
    thenWrap.setAttribute("data-branch", "then");
    thenWrap.append(renderMacroActionSummaryList(action.then ?? []));
    const elseWrap = document.createElement("ul");
    elseWrap.className = "macro-summary__branch";
    elseWrap.setAttribute("data-branch", "else");
    elseWrap.append(renderMacroActionSummaryList(action.else ?? []));
    li.append(thenWrap, elseWrap);
    return li;
  }

  if (action.action === "try_each") {
    const branches = Array.isArray(action.branches) ? action.branches : [];
    title.textContent = `try_each (${branches.length} branch${branches.length === 1 ? "" : "es"})`;
    li.append(title);
    for (let i = 0; i < branches.length; i++) {
      const branchWrap = document.createElement("div");
      branchWrap.className = "macro-summary__branch";
      const branchLabel = document.createElement("div");
      branchLabel.className = "macro-summary__meta";
      const branch = branches[i] ?? [];
      branchLabel.textContent = `branch ${i + 1} (${branch.length} action${branch.length === 1 ? "" : "s"})`;
      branchWrap.append(branchLabel);
      branchWrap.append(renderMacroActionSummaryList(branch));
      li.append(branchWrap);
    }
    return li;
  }

  title.textContent = action.action || "(unknown action)";
  if (typeof action.selector === "string") {
    const meta = document.createElement("span");
    meta.className = "macro-summary__meta";
    meta.textContent = action.selector;
    title.append(" ");
    title.append(meta);
  }
  li.append(title);
  return li;
}

export function renderMacroSelectorTools(
  sessions: SessionListResponse,
  setError: (message: string) => void,
): HTMLElement {
  const wrappers = document.createElement("div");
  wrappers.className = "macro-selector-tools";
  const sectionTitle = document.createElement("div");
  sectionTitle.className = "modal__label";
  sectionTitle.textContent = "Live selector check";

  const allSessions = [...sessions.live, ...sessions.closed];
  if (allSessions.length === 0) {
    const empty = document.createElement("p");
    empty.className = "macro-selector-tools__empty";
    empty.textContent = "No sessions available for selector check.";
    wrappers.append(sectionTitle, empty);
    return wrappers;
  }

  const controls = document.createElement("div");
  controls.className = "macro-selector-tools__controls";

  const sessionSelect = document.createElement("select");
  sessionSelect.className = "macro-selector-tools__session";
  for (const session of allSessions) {
    const option = document.createElement("option");
    option.value = session.id;
    option.textContent = session.live ? `${session.id} (live)` : `${session.id} (closed)`;
    sessionSelect.append(option);
  }

  const selectorInput = document.createElement("input");
  selectorInput.className = "macro-selector-tools__selector";
  selectorInput.type = "text";
  selectorInput.placeholder = "#submit";

  const validateBtn = document.createElement("button");
  validateBtn.className = "btn";
  validateBtn.textContent = "Validate selector";

  const validateStatus = document.createElement("div");
  validateStatus.className = "macro-selector-tools__status";

  validateBtn.addEventListener("click", () => {
    const selector = selectorInput.value.trim();
    const sessionId = sessionSelect.value;
    if (!selector) {
      setError("Enter a selector to validate.");
      return;
    }
    setError("");
    validateBtn.disabled = true;
    validateBtn.textContent = "Checking…";
    validateStatus.classList.remove("macro-selector-tools__status--error");
    validateSessionSelector(sessionId, selector)
      .then((result) => {
        validateStatus.textContent = `Selector ${result.present ? "found" : "not found"} in session ${sessionId}.`;
      })
      .catch((err: unknown) => {
        validateStatus.textContent = `Validation failed: ${String(err)}`;
        validateStatus.classList.add("macro-selector-tools__status--error");
      })
      .finally(() => {
        validateBtn.disabled = false;
        validateBtn.textContent = "Validate selector";
      });
  });

  controls.append(sessionSelect, selectorInput, validateBtn);
  wrappers.append(sectionTitle, controls, validateStatus);
  return wrappers;
}

export function renderMacroRepairPreview(preview: MacroRepairPreview): HTMLElement {
  if (preview.suggestions.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No selector-based repair suggestions.";
    return empty;
  }

  const list = document.createElement("ol");
  list.className = "repair-preview";
  for (const suggestion of preview.suggestions) {
    const item = document.createElement("li");
    item.className = "repair-preview__item";

    const title = document.createElement("div");
    title.className = "repair-preview__title";
    title.textContent = `Action ${suggestion.action_index}`;

    const prompt = document.createElement("p");
    prompt.className = "repair-preview__prompt";
    prompt.textContent = suggestion.prompt;

    item.append(title);
    if (suggestion.action_preview) {
      const previewText = document.createElement("div");
      previewText.className = "repair-preview__action";
      previewText.textContent = suggestion.action_preview;
      item.append(previewText);
    }

    if (suggestion.replacement_action) {
      const code = document.createElement("pre");
      code.className = "repair-preview__json";
      code.textContent = JSON.stringify(suggestion.replacement_action, null, 2);
      item.append(code);
    }
    item.append(prompt);
    list.append(item);
  }
  return list;
}
