// Macro panel list. Each row has edit + repair-preview buttons that route
// through the modals supplied by the dashboard composition.

import type { DashboardState } from "./dashboard-state.js";

export interface MacroListActions {
  onEdit: (name: string, sessions: DashboardState["sessions"]) => void;
  onRepairPreview: (name: string) => void;
}

export function renderMacroList(state: DashboardState, actions: MacroListActions): HTMLElement {
  const macros = state.macros;
  if (macros.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No macros saved.";
    return empty;
  }
  const ul = document.createElement("ul");
  ul.className = "macro-list";
  for (const m of macros) {
    const li = document.createElement("li");
    li.className = "macro-list__item";
    li.setAttribute("data-macro-name", m.name);
    const name = document.createElement("div");
    name.className = "macro-list__name";
    name.textContent = m.name;
    const desc = document.createElement("div");
    desc.className = "macro-list__desc";
    desc.textContent = m.description ?? "";
    li.append(name, desc);
    if (m.parameters.length > 0) {
      const params = document.createElement("div");
      params.className = "macro-list__params";
      params.textContent = `params: ${m.parameters.join(", ")}`;
      li.append(params);
    }
    const actionRow = document.createElement("div");
    actionRow.className = "macro-list__actions";
    const editBtn = document.createElement("button");
    editBtn.className = "row-action icon-btn";
    editBtn.setAttribute("aria-label", `Edit macro ${m.name}`);
    editBtn.setAttribute("title", "Edit macro");
    editBtn.setAttribute("data-testid", `macro-edit-${m.name}`);
    editBtn.textContent = "✎";
    editBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      actions.onEdit(m.name, state.sessions);
    });

    const previewBtn = document.createElement("button");
    previewBtn.className = "row-action icon-btn";
    previewBtn.setAttribute("aria-label", `Preview repair suggestions for ${m.name}`);
    previewBtn.setAttribute("title", "Repair preview");
    previewBtn.setAttribute("data-testid", `macro-repair-preview-${m.name}`);
    previewBtn.textContent = "⚑";
    previewBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      actions.onRepairPreview(m.name);
    });
    actionRow.append(editBtn, previewBtn);
    li.append(actionRow);
    ul.append(li);
  }
  return ul;
}
