import { formatDateTime, truncate } from "./format.js";
import type { DownloadEntry } from "./types.js";

function basename(path: string): string {
  if (!path) return "";
  const parts = path.split(/[/\\]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function renderDownloadsPanel(container: HTMLElement, downloads: DownloadEntry[]): void {
  container.innerHTML = "";
  container.classList.add("downloads-panel");
  container.setAttribute("data-testid", "downloads-panel");

  if (downloads.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No downloads";
    empty.setAttribute("data-testid", "downloads-empty");
    container.append(empty);
    return;
  }

  const hasSize = downloads.some((d) => typeof d.size_bytes === "number");

  const table = document.createElement("table");
  table.className = "data-table downloads-panel__table";
  table.setAttribute("data-testid", "downloads-table");

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["filename", "source", "timestamp"]) {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.append(th);
  }
  if (hasSize) {
    const th = document.createElement("th");
    th.textContent = "size";
    headRow.append(th);
  }
  const statusTh = document.createElement("th");
  statusTh.textContent = "";
  headRow.append(statusTh);
  thead.append(headRow);

  const tbody = document.createElement("tbody");
  for (const dl of downloads) {
    const tr = document.createElement("tr");
    tr.className = "downloads-panel__row";
    tr.setAttribute("data-testid", "downloads-row");

    const filenameTd = document.createElement("td");
    filenameTd.className = "downloads-panel__filename";
    const filename = dl.suggested_filename || basename(dl.path) || "(unknown)";
    filenameTd.textContent = filename;
    filenameTd.title = dl.path;
    tr.append(filenameTd);

    const urlTd = document.createElement("td");
    urlTd.className = "downloads-panel__url";
    const urlSpan = document.createElement("span");
    urlSpan.textContent = truncate(dl.url, 60);
    urlSpan.title = dl.url;
    urlTd.append(urlSpan);
    tr.append(urlTd);

    const tsTd = document.createElement("td");
    tsTd.className = "downloads-panel__ts";
    tsTd.textContent = formatDateTime(dl.timestamp);
    tsTd.title = dl.timestamp;
    tr.append(tsTd);

    if (hasSize) {
      const sizeTd = document.createElement("td");
      sizeTd.className = "downloads-panel__size";
      sizeTd.textContent = typeof dl.size_bytes === "number" ? formatBytes(dl.size_bytes) : "";
      tr.append(sizeTd);
    }

    const statusTd = document.createElement("td");
    statusTd.className = "downloads-panel__status";
    if (dl.path_exists === false) {
      const badge = document.createElement("span");
      badge.className = "download-badge download-badge--missing";
      badge.setAttribute("data-testid", "download-missing-badge");
      badge.textContent = "missing";
      statusTd.append(badge);
    }
    tr.append(statusTd);

    tbody.append(tr);
  }

  table.append(thead, tbody);
  container.append(table);
}
