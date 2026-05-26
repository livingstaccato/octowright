import { beforeEach, describe, expect, it, vi } from "vitest";
import { appendTimelineEvents, renderTimeline } from "./timeline.js";
import type { RecordingEvent } from "./types.js";

const SAMPLE_EVENTS: RecordingEvent[] = [
  { ts: "2026-04-24T13:45:00.000Z", action: "navigate", url: "https://octowright.com" },
  { ts: "2026-04-24T13:45:02.500Z", action: "click", selector: "#login" },
  { ts: "2026-04-24T13:45:05.000Z", action: "fill", value: "tim" },
  { ts: "2026-04-24T13:45:08.000Z", action: "expect_url", url: "https://octowright.com/dash" },
];

let container: HTMLDivElement;
beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
});

describe("renderTimeline", () => {
  it("renders empty placeholder when no events", () => {
    renderTimeline(container, []);
    expect(container.querySelector(".timeline__empty")?.textContent).toBe("No actions recorded yet.");
  });
  it("returns without rendering rows when the first array slot is empty", () => {
    const sparse = [] as RecordingEvent[];
    sparse.length = 1;
    renderTimeline(container, sparse);
    expect(container.querySelector("ol.timeline__list")).toBeNull();
  });
  it("renders one row per event", () => {
    renderTimeline(container, SAMPLE_EVENTS);
    const rows = container.querySelectorAll("li.timeline__row");
    expect(rows.length).toBe(4);
    expect(rows[0]?.classList.contains("timeline__row--navigate")).toBe(true);
    expect(rows[1]?.classList.contains("timeline__row--click")).toBe(true);
    expect(rows[2]?.classList.contains("timeline__row--fill")).toBe(true);
    expect(rows[3]?.classList.contains("timeline__row--expect")).toBe(true);
  });
  it("calls onSeek with event-relative seconds", () => {
    const seek = vi.fn();
    renderTimeline(container, SAMPLE_EVENTS, { onSeek: seek });
    const buttons = container.querySelectorAll<HTMLButtonElement>("button.timeline__ts");
    buttons[2]?.click();
    expect(seek).toHaveBeenCalledTimes(1);
    expect(seek.mock.calls[0]?.[0]).toBe(5);
  });
  it("encodes the headline from selector", () => {
    renderTimeline(container, SAMPLE_EVENTS);
    const headlines = container.querySelectorAll(".timeline__headline");
    expect(headlines[1]?.textContent).toBe("#login");
  });
});

describe("appendTimelineEvents", () => {
  it("appends rows to existing list", () => {
    renderTimeline(container, SAMPLE_EVENTS.slice(0, 2));
    appendTimelineEvents(container, SAMPLE_EVENTS.slice(2), SAMPLE_EVENTS[0]!.ts);
    expect(container.querySelectorAll("li.timeline__row").length).toBe(4);
  });
  it("creates a list when container was empty", () => {
    appendTimelineEvents(container, SAMPLE_EVENTS, SAMPLE_EVENTS[0]!.ts);
    expect(container.querySelector("ol.timeline__list")).not.toBeNull();
    expect(container.querySelectorAll("li.timeline__row").length).toBe(4);
  });
  it("no-ops on empty input", () => {
    appendTimelineEvents(container, [], "x");
    expect(container.children.length).toBe(0);
  });
});
