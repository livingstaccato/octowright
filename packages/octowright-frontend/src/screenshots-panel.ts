import { screenshotUrl } from "./api.js";
import { fetchDashboardMediaObjectUrl, getDashboardBearer } from "./dashboard-auth.js";
import type { ScreenshotEntry } from "./types.js";

interface ScreenshotsPanelOptions {
  fetchFn?: typeof fetch;
}

interface ScreenshotsPanelResources {
  active: boolean;
  activeLoads: number;
  fallbackCleanup: (() => void) | null;
  observer: IntersectionObserver | null;
  queue: PairedScreenshotTask[];
  tasks: PairedScreenshotTask[];
}

type ScreenshotLoadState = "idle" | "queued" | "loading" | "loaded" | "failed";

interface PairedScreenshotTask {
  cell: HTMLElement;
  controller: AbortController | null;
  failure: HTMLElement | null;
  href: string;
  img: HTMLImageElement;
  link: HTMLAnchorElement;
  objectUrl: string | null;
  state: ScreenshotLoadState;
  visible: boolean;
}

const panelResources = new WeakMap<HTMLElement, ScreenshotsPanelResources>();
const MAX_CONCURRENT_SCREENSHOT_FETCHES = 3;
const VIEWPORT_MARGIN_PX = 200;

export function disposeScreenshotsPanel(container: HTMLElement): void {
  const resources = panelResources.get(container);
  if (!resources) return;
  resources.active = false;
  resources.observer?.disconnect();
  resources.fallbackCleanup?.();
  for (const task of resources.tasks) {
    task.controller?.abort();
    if (task.objectUrl) URL.revokeObjectURL(task.objectUrl);
  }
  panelResources.delete(container);
}

function resetLoadedTask(task: PairedScreenshotTask): void {
  if (task.objectUrl) URL.revokeObjectURL(task.objectUrl);
  task.objectUrl = null;
  task.img.removeAttribute("src");
  task.link.removeAttribute("href");
  task.link.setAttribute("aria-disabled", "true");
}

function setTaskVisible(task: PairedScreenshotTask, visible: boolean, resources: ScreenshotsPanelResources): void {
  task.visible = visible;
  if (visible) {
    if (task.state === "idle") {
      task.state = "queued";
      resources.queue.push(task);
    }
    return;
  }

  if (task.state === "queued") {
    task.state = "idle";
    resources.queue = resources.queue.filter((queued) => queued !== task);
  }
  if (task.state === "loading") {
    task.state = "idle";
    task.controller?.abort();
  }
  if (task.state === "loaded") {
    resetLoadedTask(task);
    task.state = "idle";
  }
  if (task.state === "failed") {
    task.failure?.remove();
    task.failure = null;
    task.state = "idle";
  }
}

function pumpScreenshotQueue(resources: ScreenshotsPanelResources, options: ScreenshotsPanelOptions): void {
  while (resources.active && resources.activeLoads < MAX_CONCURRENT_SCREENSHOT_FETCHES) {
    const task = resources.queue.shift();
    if (!task) return;
    if (task.state !== "queued" || !task.visible) continue;

    const controller = new AbortController();
    task.controller = controller;
    task.state = "loading";
    resources.activeLoads += 1;
    void fetchDashboardMediaObjectUrl(task.href, {
      signal: controller.signal,
      ...(options.fetchFn ? { fetchFn: options.fetchFn } : {}),
    })
      .then((objectUrl) => {
        if (!resources.active || controller.signal.aborted || !task.visible) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        task.objectUrl = objectUrl;
        task.state = "loaded";
        task.img.src = objectUrl;
        task.link.href = objectUrl;
        task.link.removeAttribute("aria-disabled");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || !resources.active) return;
        task.state = "failed";
        const failure = document.createElement("span");
        failure.className = "screenshots-panel__error";
        failure.setAttribute("role", "status");
        failure.textContent = `preview unavailable: ${(error as Error).message}`;
        task.failure = failure;
        task.cell.append(failure);
      })
      .finally(() => {
        if (task.controller === controller) {
          task.controller = null;
          if (task.state === "loading") task.state = "idle";
        }
        resources.activeLoads -= 1;
        pumpScreenshotQueue(resources, options);
      });
  }
}

function installIntersectionLoading(resources: ScreenshotsPanelResources, options: ScreenshotsPanelOptions): void {
  const tasksByCell = new Map<Element, PairedScreenshotTask>();
  for (const task of resources.tasks) tasksByCell.set(task.cell, task);
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const task = tasksByCell.get(entry.target);
        if (task) setTaskVisible(task, entry.isIntersecting, resources);
      }
      pumpScreenshotQueue(resources, options);
    },
    { rootMargin: `${VIEWPORT_MARGIN_PX}px 0px` },
  );
  resources.observer = observer;
  for (const task of resources.tasks) observer.observe(task.cell);
}

function nearViewport(element: HTMLElement): boolean {
  const rect = element.getBoundingClientRect();
  return (
    rect.bottom >= -VIEWPORT_MARGIN_PX &&
    rect.top <= window.innerHeight + VIEWPORT_MARGIN_PX &&
    rect.right >= 0 &&
    rect.left <= window.innerWidth
  );
}

function installFallbackLoading(resources: ScreenshotsPanelResources, options: ScreenshotsPanelOptions): void {
  let animationFrame: number | null = null;
  const updateVisibility = () => {
    animationFrame = null;
    for (const task of resources.tasks) setTaskVisible(task, nearViewport(task.cell), resources);
    pumpScreenshotQueue(resources, options);
  };
  const scheduleUpdate = () => {
    if (animationFrame === null) animationFrame = window.requestAnimationFrame(updateVisibility);
  };
  window.addEventListener("scroll", scheduleUpdate, { passive: true });
  window.addEventListener("resize", scheduleUpdate);
  resources.fallbackCleanup = () => {
    window.removeEventListener("scroll", scheduleUpdate);
    window.removeEventListener("resize", scheduleUpdate);
    if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
  };
  updateVisibility();
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

  const resources: ScreenshotsPanelResources = {
    active: true,
    activeLoads: 0,
    fallbackCleanup: null,
    observer: null,
    queue: [],
    tasks: [],
  };
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
      resources.tasks.push({
        cell,
        controller: null,
        failure: null,
        href,
        img,
        link,
        objectUrl: null,
        state: "idle",
        visible: false,
      });
    }
  }

  container.append(grid);
  if (paired) {
    if (typeof IntersectionObserver === "function") installIntersectionLoading(resources, options);
    else installFallbackLoading(resources, options);
  }
}
