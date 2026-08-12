import { screenshotUrl } from "./api.js";
import { fetchDashboardMediaObjectUrl, getDashboardBearer } from "./dashboard-auth.js";
import type { ScreenshotEntry } from "./types.js";

interface ScreenshotsPanelOptions {
  fetchFn?: typeof fetch;
}

interface ScreenshotsPanelResources {
  active: boolean;
  controllers: AbortController[];
  objectUrls: string[];
}

const panelResources = new WeakMap<HTMLElement, ScreenshotsPanelResources>();

export function disposeScreenshotsPanel(container: HTMLElement): void {
  const resources = panelResources.get(container);
  if (!resources) return;
  resources.active = false;
  for (const controller of resources.controllers) controller.abort();
  for (const objectUrl of resources.objectUrls) URL.revokeObjectURL(objectUrl);
  panelResources.delete(container);
}

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
  options: ScreenshotsPanelOptions = {},
): void {
  disposeScreenshotsPanel(container);
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

  const resources: ScreenshotsPanelResources = { active: true, controllers: [], objectUrls: [] };
  panelResources.set(container, resources);
  const paired = getDashboardBearer() !== null;

  const grid = document.createElement("div");
  grid.className = "screenshots-panel__grid";
  grid.setAttribute("data-testid", "screenshots-grid");

  for (const shot of screenshots) {
    const cell = document.createElement("figure");
    cell.className = "screenshots-panel__cell";
    cell.setAttribute("data-testid", "screenshot-cell");

    const href = screenshotUrl(sessionId, shot.filename);
    const link = document.createElement("a");
    if (!paired) link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.setAttribute("data-testid", "screenshot-link");

    const img = document.createElement("img");
    img.className = "screenshots-panel__img";
    if (!paired) img.src = href;
    img.alt = shot.filename;
    img.loading = "lazy";
    link.append(img);

    const caption = document.createElement("figcaption");
    caption.className = "screenshots-panel__caption";
    const time = formatHms(shot.ts);
    caption.textContent = time ? `[${time}] ${shot.filename}` : shot.filename;

    cell.append(link, caption);
    grid.append(cell);

    if (paired) {
      link.setAttribute("aria-disabled", "true");
      const controller = new AbortController();
      resources.controllers.push(controller);
      void fetchDashboardMediaObjectUrl(href, {
        signal: controller.signal,
        ...(options.fetchFn ? { fetchFn: options.fetchFn } : {}),
      })
        .then((objectUrl) => {
          if (!resources.active || controller.signal.aborted) {
            URL.revokeObjectURL(objectUrl);
            return;
          }
          resources.objectUrls.push(objectUrl);
          img.src = objectUrl;
          link.href = objectUrl;
          link.removeAttribute("aria-disabled");
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted || !resources.active) return;
          const failure = document.createElement("span");
          failure.className = "screenshots-panel__error";
          failure.setAttribute("role", "status");
          failure.textContent = `preview unavailable: ${(error as Error).message}`;
          cell.append(failure);
        });
    }
  }

  container.append(grid);
}
