import { beforeEach, describe, expect, it } from "vitest";
import { renderScreenshotsPanel } from "./screenshots-panel.js";
import type { ScreenshotEntry } from "./types.js";

const SAMPLE: ScreenshotEntry[] = [
  {
    path: "/tmp/sess/shot-001.png",
    filename: "shot-001.png",
    ts: 1745493600, // 2025-04-24T...
    size_bytes: 12345,
  },
  {
    path: "/tmp/sess/shot-002.png",
    filename: "shot 002.png",
    ts: 1745493660,
    size_bytes: 23456,
  },
];

let container: HTMLDivElement;
beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
});

describe("renderScreenshotsPanel", () => {
  it("renders empty placeholder when none", () => {
    renderScreenshotsPanel(container, "sess-1", []);
    const empty = container.querySelector('[data-testid="screenshots-empty"]');
    expect(empty?.textContent).toBe("No screenshots taken yet");
  });

  it("renders one cell per screenshot", () => {
    renderScreenshotsPanel(container, "sess-1", SAMPLE);
    const cells = container.querySelectorAll('[data-testid="screenshot-cell"]');
    expect(cells.length).toBe(2);
  });

  it("wraps each thumbnail in an anchor with the screenshot URL", () => {
    renderScreenshotsPanel(container, "sess-1", SAMPLE);
    const links = container.querySelectorAll<HTMLAnchorElement>('[data-testid="screenshot-link"]');
    expect(links.length).toBe(2);
    expect(links[0]?.getAttribute("href")).toBe("/api/sessions/sess-1/screenshots/shot-001.png");
    // filename with space gets percent-encoded
    expect(links[1]?.getAttribute("href")).toBe("/api/sessions/sess-1/screenshots/shot%20002.png");
    expect(links[0]?.target).toBe("_blank");
  });

  it("uses lazy-loaded img inside the link", () => {
    renderScreenshotsPanel(container, "sess-1", SAMPLE);
    const imgs = container.querySelectorAll<HTMLImageElement>(".screenshots-panel__img");
    expect(imgs.length).toBe(2);
    expect(imgs[0]?.loading).toBe("lazy");
    expect(imgs[0]?.alt).toBe("shot-001.png");
  });

  it("renders caption with timestamp and filename", () => {
    renderScreenshotsPanel(container, "sess-1", SAMPLE);
    const captions = container.querySelectorAll(".screenshots-panel__caption");
    expect(captions[0]?.textContent).toMatch(/^\[\d{2}:\d{2}:\d{2}\] shot-001\.png$/);
  });

  it("encodes session id in href", () => {
    renderScreenshotsPanel(container, "a/b", SAMPLE.slice(0, 1));
    const link = container.querySelector<HTMLAnchorElement>('[data-testid="screenshot-link"]');
    expect(link?.getAttribute("href")).toBe("/api/sessions/a%2Fb/screenshots/shot-001.png");
  });
});
