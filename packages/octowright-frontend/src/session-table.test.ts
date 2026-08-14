import { describe, expect, it, vi } from "vitest";
import { renderSessionTable } from "./session-table.js";
import type { OperationGateSnapshot, SessionSummary } from "./types.js";

const row: SessionSummary = {
  id: "s/1",
  kind: "chromium",
  label: null,
  profile: null,
  url: null,
  started_at: "bad-date",
  live: false,
  log_path: "s1.jsonl",
};

const IDLE_GATE: OperationGateSnapshot = {
  state: "open",
  active_operation: null,
  active_for_ms: null,
  queue_depth: 0,
  oldest_wait_ms: null,
  queue_timeout_seconds: 300,
};

describe("renderSessionTable", () => {
  it("renders live and closed empty states", () => {
    const actions = { onRelaunch: vi.fn(), onDelete: vi.fn() };
    expect(renderSessionTable([], true, actions).textContent).toBe("No live sessions.");
    expect(renderSessionTable([], false, actions).textContent).toBe("No closed sessions yet.");
  });

  it("renders null label/profile/url as empty cells", () => {
    const table = renderSessionTable([{ ...row, live: true }], true, { onRelaunch: vi.fn(), onDelete: vi.fn() });
    expect(table.querySelector("a")?.getAttribute("href")).toBe("/sessions/s%2F1");
    expect(table.textContent).toContain("bad-date");
  });

  it("shows lock badge and css class for protected sessions", () => {
    const table = renderSessionTable([{ ...row, live: true, protected: true }], true, {
      onRelaunch: vi.fn(),
      onDelete: vi.fn(),
    });
    const badge = table.querySelector(".protected-badge");
    expect(badge).not.toBeNull();
    expect(badge?.getAttribute("title")).toBe("Protected — close-capable tools require force=True");
    expect(table.querySelector("tr.protected-session")).not.toBeNull();
  });

  it("shows no lock badge for unprotected sessions", () => {
    const table = renderSessionTable([{ ...row, live: true, protected: false }], true, {
      onRelaunch: vi.fn(),
      onDelete: vi.fn(),
    });
    expect(table.querySelector(".protected-badge")).toBeNull();
    expect(table.querySelector("tr.protected-session")).toBeNull();
  });

  it("wires closed-session relaunch and delete buttons", () => {
    const onRelaunch = vi.fn();
    const onDelete = vi.fn();
    const table = renderSessionTable([row], false, { onRelaunch, onDelete });

    table.querySelector<HTMLButtonElement>('[aria-label="Relaunch with same params"]')?.click();
    table.querySelector<HTMLButtonElement>('[aria-label="Delete recording"]')?.click();

    expect(onRelaunch).toHaveBeenCalledWith("s/1");
    expect(onDelete).toHaveBeenCalledWith("s/1");
  });

  it("shows busy operation and queue depth", () => {
    const actions = { onRelaunch: vi.fn(), onDelete: vi.fn() };
    const table = renderSessionTable(
      [
        {
          ...row,
          live: true,
          operation_gate: {
            state: "open",
            active_operation: "macro_run",
            active_for_ms: 1250,
            queue_depth: 2,
            oldest_wait_ms: 900,
            queue_timeout_seconds: 300,
          },
        },
      ],
      true,
      actions,
    );
    expect(table.querySelector(".operation-badge")?.textContent).toBe("busy macro_run +2");
  });

  it.each(["closing", "broken"] as const)("shows %s state", (state) => {
    const actions = { onRelaunch: vi.fn(), onDelete: vi.fn() };
    const table = renderSessionTable([{ ...row, live: true, operation_gate: { ...IDLE_GATE, state } }], true, actions);
    expect(table.querySelector(`.operation-badge--${state}`)?.textContent).toBe(state);
  });

  it("is quiet for idle browsers, closed rows, and terminals", () => {
    const actions = { onRelaunch: vi.fn(), onDelete: vi.fn() };
    const rows: SessionSummary[] = [
      { ...row, live: true, operation_gate: IDLE_GATE },
      { ...row, id: "closed", live: false, operation_gate: { ...IDLE_GATE, state: "closed" as const } },
      { ...row, id: "terminal", kind: "terminal" as const, live: true },
    ];
    expect(renderSessionTable(rows, true, actions).querySelector(".operation-badge")).toBeNull();
  });
});
