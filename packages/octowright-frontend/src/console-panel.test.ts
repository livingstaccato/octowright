import { beforeEach, describe, expect, it } from "vitest";
import { badgeClassForLevel, filterMessages, renderConsolePanel } from "./console-panel.js";
import type { ConsoleMessage } from "./types.js";

const SAMPLE: ConsoleMessage[] = [
  { level: "log", text: "hello", page_index: null },
  { level: "warn", text: "watch out", page_index: 1 },
  { level: "error", text: "boom", page_index: null },
  { level: "info", text: "fyi", page_index: 2 },
];

let container: HTMLDivElement;
beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
});

describe("badgeClassForLevel", () => {
  it("maps known levels", () => {
    expect(badgeClassForLevel("log")).toBe("console-badge--log");
    expect(badgeClassForLevel("warn")).toBe("console-badge--warn");
    expect(badgeClassForLevel("error")).toBe("console-badge--error");
    expect(badgeClassForLevel("info")).toBe("console-badge--info");
  });
  it("falls back for unknown", () => {
    expect(badgeClassForLevel("trace")).toBe("console-badge--log");
  });
});

describe("filterMessages", () => {
  it("returns all when level is 'all'", () => {
    expect(filterMessages(SAMPLE, "all")).toHaveLength(4);
  });
  it("filters by level", () => {
    const errs = filterMessages(SAMPLE, "error");
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe("boom");
  });
});

describe("renderConsolePanel", () => {
  it("renders one row per message with level badge", () => {
    renderConsolePanel(container, SAMPLE);
    const rows = container.querySelectorAll("li.console-panel__row");
    expect(rows.length).toBe(4);
    expect(rows[0]?.querySelector(".console-badge--log")).not.toBeNull();
    expect(rows[1]?.querySelector(".console-badge--warn")).not.toBeNull();
    expect(rows[2]?.querySelector(".console-badge--error")).not.toBeNull();
    expect(rows[3]?.querySelector(".console-badge--info")).not.toBeNull();
  });

  it("prefixes [tab N] when page_index is non-null", () => {
    renderConsolePanel(container, SAMPLE);
    const rows = container.querySelectorAll("li.console-panel__row");
    // row 0 has page_index null — no tab span
    expect(rows[0]?.querySelector(".console-panel__tab")).toBeNull();
    // row 1 has page_index 1
    expect(rows[1]?.querySelector(".console-panel__tab")?.textContent).toBe("[tab 1]");
    expect(rows[3]?.querySelector(".console-panel__tab")?.textContent).toBe("[tab 2]");
  });

  it("shows empty placeholder when no messages", () => {
    renderConsolePanel(container, []);
    const empty = container.querySelector('[data-testid="console-empty"]') as HTMLElement | null;
    expect(empty?.textContent).toBe("No console messages");
    expect(empty?.style.display).not.toBe("none");
  });

  it("level filter re-renders client-side", () => {
    renderConsolePanel(container, SAMPLE);
    const select = container.querySelector<HTMLSelectElement>('[data-testid="console-filter"]');
    expect(select).not.toBeNull();
    if (!select) throw new Error("no select");
    select.value = "error";
    select.dispatchEvent(new Event("change"));
    const rows = container.querySelectorAll("li.console-panel__row");
    expect(rows.length).toBe(1);
    expect(rows[0]?.textContent).toContain("boom");
  });

  it("respects initialLevel option", () => {
    renderConsolePanel(container, SAMPLE, { initialLevel: "warn" });
    const rows = container.querySelectorAll("li.console-panel__row");
    expect(rows.length).toBe(1);
    expect(rows[0]?.textContent).toContain("watch out");
  });
});
