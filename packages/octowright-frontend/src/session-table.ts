// Live + closed session tables. Closed-session rows include relaunch and
// delete buttons that route through the action handlers in dashboard.ts.

import { formatDateTime, shortUrl } from "./format.js";
import { cell, linkCell, textCell } from "./table-helpers.js";
import type { SessionSummary } from "./types.js";

export interface SessionTableActions {
  onRelaunch: (id: string) => void;
  onDelete: (id: string) => void;
}

export function renderSessionTable(
  rows: SessionSummary[],
  live: boolean,
  actions: SessionTableActions,
): HTMLElement {
  if (rows.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = live ? "No live sessions." : "No closed sessions yet.";
    return empty;
  }
  const table = document.createElement("table");
  table.className = "data-table";
  table.setAttribute("data-testid", live ? "table-live-sessions" : "table-closed-sessions");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const col of ["", "id", "kind", "profile / label", "url", "started"]) {
    const th = document.createElement("th");
    th.textContent = col;
    headRow.append(th);
  }
  if (!live) {
    const thActions = document.createElement("th");
    thActions.className = "col-actions";
    headRow.append(thActions);
  }
  thead.append(headRow);

  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.setAttribute("data-session-id", row.id);
    if (row.protected) tr.classList.add("protected-session");
    const lockTd = document.createElement("td");
    lockTd.className = "col-protected";
    if (row.protected) {
      const lock = document.createElement("span");
      lock.className = "protected-badge";
      lock.textContent = "🔒";
      lock.setAttribute("title", "Protected — close-capable tools require force=True");
      lockTd.append(lock);
    }
    tr.append(
      lockTd,
      cell(linkCell(row.id, `/sessions/${encodeURIComponent(row.id)}`)),
      cell(textCell(row.kind)),
      cell(textCell(row.label ?? row.profile ?? "")),
      cell(textCell(shortUrl(row.url, 80))),
      cell(textCell(formatDateTime(row.started_at))),
    );
    if (!live) {
      const actionTd = document.createElement("td");
      actionTd.className = "col-actions";
      const relaunchBtn = document.createElement("button");
      relaunchBtn.className = "row-action icon-btn";
      relaunchBtn.setAttribute("aria-label", "Relaunch with same params");
      relaunchBtn.setAttribute("title", "Relaunch like this — same kind/profile/url");
      relaunchBtn.textContent = "↻";
      relaunchBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        actions.onRelaunch(row.id);
      });
      const delBtn = document.createElement("button");
      delBtn.className = "row-action icon-btn--danger";
      delBtn.setAttribute("aria-label", "Delete recording");
      delBtn.setAttribute("title", "Delete recording files from disk");
      delBtn.textContent = "⊗";
      delBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        actions.onDelete(row.id);
      });
      actionTd.append(relaunchBtn, delBtn);
      tr.append(actionTd);
    }
    tbody.append(tr);
  }
  table.append(thead, tbody);
  const scroll = document.createElement("div");
  scroll.className = "table-scroll";
  scroll.append(table);
  return scroll;
}
