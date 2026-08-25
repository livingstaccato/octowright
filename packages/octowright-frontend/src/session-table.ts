// Live + closed session tables. Closed-session rows include relaunch and
// delete buttons that route through the action handlers in dashboard.ts.

import { formatDateTime, shortUrl } from "./format.js";
import { cell, linkCell, textCell } from "./table-helpers.js";
import type { SessionSummary } from "./types.js";

export interface SessionTableActions {
  onRelaunch: (id: string) => void;
  onDelete: (id: string) => void;
}

/** CSS-class suffix for the badge; idle/closed gates never render one at all,
 * so there is no fourth variant to represent here. */
type OperationBadgeRenderState = "busy" | "closing" | "broken";

function operationBadge(row: SessionSummary): HTMLElement | null {
  // No kind check: `operation_gate` is present only when the session actually
  // supplies an `operation_snapshot()` (see `http/discovery._live_summary`), so
  // `!gate` already covers every kind that has no gate. Naming a kind here was
  // both dead and wrong in principle -- a plugin kind that DOES expose a gate
  // should get a badge, and a hardcoded exclusion would deny it one.
  const gate = row.operation_gate;
  if (!gate || !row.live) return null;
  let text: string | null = null;
  let renderState: OperationBadgeRenderState | null = null;
  if (gate.state === "open" && gate.active_operation) {
    renderState = "busy";
    text = `busy ${gate.active_operation}${gate.queue_depth > 0 ? ` +${gate.queue_depth}` : ""}`;
  } else if (gate.state === "closing" || gate.state === "broken") {
    renderState = gate.state;
    text = gate.state;
  }
  if (text === null || renderState === null) return null;
  const badge = document.createElement("span");
  badge.className = `operation-badge operation-badge--${renderState}`;
  badge.textContent = text;
  badge.setAttribute("role", "status");
  return badge;
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
    const badge = operationBadge(row);
    if (badge) lockTd.append(badge);
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
