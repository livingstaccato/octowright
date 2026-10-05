import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EventsResponse } from "./types.js";

const { getEventsMock } = vi.hoisted(() => ({ getEventsMock: vi.fn() }));
vi.mock("./api.js", () => ({ getEvents: getEventsMock }));

const { getAllEvents } = await import("./events-pager.js");

function page(actions: string[], cursor: number, complete: boolean, totalBytes = 300): EventsResponse {
  return {
    events: actions.map((action) => ({ ts: "2026-10-04T00:00:00Z", action })),
    cursor,
    total_bytes: totalBytes,
    complete,
  };
}

beforeEach(() => {
  getEventsMock.mockReset();
});

describe("getAllEvents", () => {
  it("follows the cursor until the server says the recording is complete", async () => {
    getEventsMock
      .mockResolvedValueOnce(page(["launch", "navigate"], 100, false))
      .mockResolvedValueOnce(page(["click"], 200, false))
      .mockResolvedValueOnce(page(["close"], 300, true));

    const all = await getAllEvents("sess-big");

    expect(all.events.map((e) => e.action)).toEqual(["launch", "navigate", "click", "close"]);
    expect(all.cursor).toBe(300);
    expect(all.complete).toBe(true);
    expect(getEventsMock.mock.calls).toEqual([
      ["sess-big", 0],
      ["sess-big", 100],
      ["sess-big", 200],
    ]);
  });

  it("asks once when the first page is the whole recording", async () => {
    getEventsMock.mockResolvedValueOnce(page(["launch"], 50, true, 50));
    const all = await getAllEvents("sess-small");
    expect(all.events).toHaveLength(1);
    expect(getEventsMock).toHaveBeenCalledTimes(1);
  });

  it("stops when a page does not move the cursor and keeps the furthest cursor", async () => {
    // A live recording's half-written last line holds the cursor in place.
    getEventsMock
      .mockResolvedValueOnce(page(["launch"], 120, false))
      .mockResolvedValueOnce(page([], 120, false))
      .mockResolvedValueOnce(page(["never"], 999, true));

    const all = await getAllEvents("sess-live");

    expect(all.events.map((e) => e.action)).toEqual(["launch"]);
    expect(all.cursor).toBe(120);
    expect(getEventsMock).toHaveBeenCalledTimes(2);
  });

  it("ignores a page whose cursor went backwards", async () => {
    getEventsMock.mockResolvedValueOnce(page(["launch"], 120, false)).mockResolvedValueOnce(page(["stale"], 0, false));

    const all = await getAllEvents("sess-odd");

    expect(all.events.map((e) => e.action)).toEqual(["launch"]);
    expect(all.cursor).toBe(120);
  });

  it("starts from the given cursor", async () => {
    getEventsMock.mockResolvedValueOnce(page(["later"], 400, true));
    await getAllEvents("sess-x", 300);
    expect(getEventsMock).toHaveBeenCalledWith("sess-x", 300);
  });
});
