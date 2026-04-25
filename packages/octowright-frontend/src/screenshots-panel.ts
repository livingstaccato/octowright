import { screenshotUrl } from "./api.js";
import type { ScreenshotEntry } from "./types.js";

function formatHms(epochSeconds: number): string {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return "";
  const ms = epochSeconds * 1000;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "";
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

export function renderScreenshotsPanel(
  container: HTMLElement,
  sessionId: string,
  screenshots: ScreenshotEntry[],
): void {
  container.innerHTML = "";
  container.classList.add("screenshots-panel");
  container.setAttribute("data-testid", "screenshots-panel");

  if (screenshots.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No screenshots taken yet";
    empty.setAttribute("data-testid", "screenshots-empty");
    container.append(empty);
    return;
  }

  const grid = document.createElement("div");
  grid.className = "screenshots-panel__grid";
  grid.setAttribute("data-testid", "screenshots-grid");

  for (const shot of screenshots) {
    const cell = document.createElement("figure");
    cell.className = "screenshots-panel__cell";
    cell.setAttribute("data-testid", "screenshot-cell");

    const href = screenshotUrl(sessionId, shot.filename);
    const link = document.createElement("a");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.setAttribute("data-testid", "screenshot-link");

    const img = document.createElement("img");
    img.className = "screenshots-panel__img";
    img.src = href;
    img.alt = shot.filename;
    img.loading = "lazy";
    link.append(img);

    const caption = document.createElement("figcaption");
    caption.className = "screenshots-panel__caption";
    const time = formatHms(shot.ts);
    caption.textContent = time ? `[${time}] ${shot.filename}` : shot.filename;

    cell.append(link, caption);
    grid.append(cell);
  }

  container.append(grid);
}
