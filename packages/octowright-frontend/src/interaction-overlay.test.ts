import { afterEach, describe, expect, it, vi } from "vitest";
import { DASHBOARD_AUTH_REQUIRED_EVENT } from "./dashboard-auth.js";
import { mountInteractionOverlay } from "./interaction-overlay.js";
import type { InteractionSidecarRow, InteractionsResponse } from "./types.js";

const rows: InteractionSidecarRow[] = [
  {
    type: "page",
    v: 1,
    page_id: 1,
    video: "one.webm",
    time_origin: 1000000000000,
    time_origin_kind: "estimate",
    video_geometry: { engine: "chromium", viewport_width: 640, viewport_height: 480, device_scale_factor: 1 },
  },
  {
    type: "interaction",
    v: 1,
    page_id: 1,
    video: null,
    t: 100,
    at: 1000000000100,
    phase: "down",
    pointer_type: "mouse",
    pointer_id: 1,
    button: 0,
    x: 160,
    y: 240,
    width: 640,
    height: 480,
    shown: false,
  },
];
const reply = (events = rows, extra: Partial<InteractionsResponse> = {}): InteractionsResponse => ({
  available: true,
  events,
  next_cursor: 100,
  complete: true,
  truncated: false,
  ...extra,
});
const flush = async () => {
  for (let i = 0; i < 6; i++) await Promise.resolve();
};
function setup(fetchPage = vi.fn(async () => reply())) {
  const container = document.createElement("div"),
    video = document.createElement("video");
  container.append(video);
  document.body.append(container);
  Object.defineProperties(video, {
    videoWidth: { value: 320, configurable: true },
    videoHeight: { value: 240, configurable: true },
    paused: { value: true, configurable: true },
  });
  video.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 240 }) as DOMRect;
  const frames = new Map<number, FrameRequestCallback>();
  let id = 0;
  const handle = mountInteractionOverlay(container, {
    sessionId: "s",
    video,
    videoBasename: "one.webm",
    fetchPage,
    requestFrame: (cb) => {
      frames.set(++id, cb);
      return id;
    },
    cancelFrame: (n) => {
      frames.delete(n);
    },
  });
  const checkbox = container.querySelector("input")!;
  return { container, video, handle, checkbox, frames, fetchPage };
}
afterEach(() => {
  window.dispatchEvent(new Event(DASHBOARD_AUTH_REQUIRED_EVENT));
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});
describe("playback overlay lifecycle", () => {
  it("labels loading, legacy unavailable, empty capture, network failure and truncation distinctly", async () => {
    for (const [response, text] of [
      [reply([], { available: false }), "unavailable"],
      [reply([]), "No interactions"],
      [reply([], { truncated: true }), "truncated"],
    ] as const) {
      const view = setup(vi.fn(async () => response));
      expect(view.container.textContent).toContain("Loading");
      await flush();
      expect(view.container.textContent).toContain(text);
      expect(view.checkbox.disabled).toBe(true);
      view.handle.destroy();
    }
    const view = setup(
      vi.fn(async () => {
        throw Error("offline");
      }),
    );
    await flush();
    expect(view.container.textContent).toContain("Could not load");
  });
  it("paginates with one signal and aggregates later truncation", async () => {
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce(reply([rows[0]!], { complete: false, next_cursor: 50 }))
      .mockResolvedValueOnce(reply([rows[1]!], { truncated: true }));
    const view = setup(fetchPage);
    await flush();
    expect(fetchPage.mock.calls.map((c) => c[1])).toEqual([0, 50]);
    expect(fetchPage.mock.calls[0]?.[3]).toBe(fetchPage.mock.calls[1]?.[3]);
    expect(view.container.textContent).toContain("truncated");
  });
  it.each([0, -1, NaN, 64 * 1024 * 1024 + 1])("stops invalid or excessive byte cursor %s", async (cursor) => {
    const view = setup(vi.fn(async () => reply(rows, { complete: false, next_cursor: cursor })));
    await flush();
    expect(view.fetchPage).toHaveBeenCalledTimes(1);
    expect(view.container.textContent).toContain("incomplete");
  });
  it("draws from currentTime on pause, seek, rate and resize; toggling creates at most one loop", async () => {
    const view = setup();
    await flush();
    view.checkbox.checked = true;
    view.checkbox.dispatchEvent(new Event("change"));
    view.video.currentTime = 0.6;
    view.video.dispatchEvent(new Event("timeupdate"));
    const marker = () => view.container.querySelector<HTMLElement>(".interaction-marker");
    expect(marker()?.textContent).toBe("Hold");
    expect(marker()?.style.left).toBe("50px");
    expect(marker()?.style.top).toBe("75px");
    view.video.playbackRate = 2;
    view.video.dispatchEvent(new Event("ratechange"));
    expect(marker()?.textContent).toBe("Hold");
    view.video.currentTime = 0.05;
    view.video.dispatchEvent(new Event("seeking"));
    expect(marker()).toBeNull();
    Object.defineProperty(view.video, "paused", { value: false, configurable: true });
    view.video.dispatchEvent(new Event("play"));
    view.video.dispatchEvent(new Event("play"));
    expect(view.frames.size).toBe(1);
    Object.defineProperty(view.video, "paused", { value: true, configurable: true });
    view.video.dispatchEvent(new Event("pause"));
    expect(view.frames.size).toBe(0);
    view.video.currentTime = 0.2;
    view.video.dispatchEvent(new Event("resize"));
    expect(marker()?.textContent).toBe("Click");
    view.checkbox.checked = false;
    view.checkbox.dispatchEvent(new Event("change"));
    expect(marker()).toBeNull();
    view.handle.destroy();
    view.handle.destroy();
    expect(view.container.querySelector("input")).toBeNull();
  });
  it("waits for native metadata and explains unknown/unsupported geometry", async () => {
    const view = setup(
      vi.fn(async () => reply(rows.map((r) => (r.type === "page" ? { ...r, video_geometry: null } : r)))),
    );
    await flush();
    view.checkbox.checked = true;
    view.checkbox.dispatchEvent(new Event("change"));
    view.video.currentTime = 0.2;
    view.video.dispatchEvent(new Event("loadedmetadata"));
    expect(view.container.textContent).toContain("geometry unavailable");
    expect(view.container.textContent).toContain("Any live indicators already recorded remain visible.");
    expect(view.container.querySelector(".interaction-marker")).toBeNull();
  });
  it("changes selected video without mixing contacts", async () => {
    const view = setup();
    await flush();
    view.checkbox.checked = true;
    view.video.currentTime = 0.2;
    view.checkbox.dispatchEvent(new Event("change"));
    expect(view.container.querySelector(".interaction-marker")).not.toBeNull();
    view.handle.setVideo("two.webm");
    expect(view.container.querySelector(".interaction-marker")).toBeNull();
    expect(view.container.textContent).toContain("No interactions");
  });
  it("aborts on auth loss, ignores late responses and preserves replacement pairing UI", async () => {
    let resolve!: (r: InteractionsResponse) => void;
    const fetchPage = vi.fn(
      () =>
        new Promise<InteractionsResponse>((r) => {
          resolve = r;
        }),
    );
    const view = setup(fetchPage);
    window.dispatchEvent(new Event(DASHBOARD_AUTH_REQUIRED_EVENT));
    view.container.replaceChildren(document.createTextNode("Pair dashboard"));
    resolve(reply());
    await flush();
    expect(fetchPage.mock.calls[0]?.[3].aborted).toBe(true);
    expect(view.container.textContent).toBe("Pair dashboard");
    expect(view.frames.size).toBe(0);
  });
});

it("runs one frame chain, freezes paused progress and rebuilds when re-enabled", async () => {
  const view = setup();
  await flush();
  view.checkbox.checked = true;
  view.checkbox.dispatchEvent(new Event("change"));
  view.video.currentTime = 0.3;
  view.video.playbackRate = 2;
  view.video.dispatchEvent(new Event("ratechange"));
  const marker = () => view.container.querySelector<HTMLElement>(".interaction-marker");
  expect(marker()?.textContent).toBe("Click");
  expect(marker()?.style.getPropertyValue("--hold-progress")).toBe("0.4");
  Object.defineProperty(view.video, "paused", { value: false, configurable: true });
  view.video.dispatchEvent(new Event("play"));
  const [id, callback] = [...view.frames][0]!;
  view.frames.delete(id);
  view.video.currentTime = 0.6;
  callback(0);
  expect(marker()?.textContent).toBe("Hold");
  expect(view.frames.size).toBe(1);
  Object.defineProperty(view.video, "paused", { value: true, configurable: true });
  view.video.dispatchEvent(new Event("pause"));
  expect(view.frames.size).toBe(0);
  view.video.dispatchEvent(new Event("timeupdate"));
  expect(marker()?.style.getPropertyValue("--hold-progress")).toBe("1");
  view.checkbox.checked = false;
  view.checkbox.dispatchEvent(new Event("change"));
  view.video.currentTime = 0.2;
  view.checkbox.checked = true;
  view.checkbox.dispatchEvent(new Event("change"));
  expect(marker()?.textContent).toBe("Click");
});
it("redraws after metadata and ResizeObserver delivery and disconnects on disposal", async () => {
  let notify!: ResizeObserverCallback;
  const disconnect = vi.fn();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: ResizeObserverCallback) {
        notify = callback;
      }
      observe() {}
      disconnect = disconnect;
    },
  );
  const view = setup();
  await flush();
  view.video.currentTime = 0.2;
  view.checkbox.checked = true;
  Object.defineProperty(view.video, "videoWidth", { value: 0, configurable: true });
  view.checkbox.dispatchEvent(new Event("change"));
  expect(view.container.textContent).toContain("Waiting for video dimensions");
  expect(view.container.querySelector(".interaction-marker")).toBeNull();
  Object.defineProperty(view.video, "videoWidth", { value: 320, configurable: true });
  view.video.dispatchEvent(new Event("loadedmetadata"));
  expect(view.container.querySelector(".interaction-marker")).not.toBeNull();
  view.video.getBoundingClientRect = () => ({ left: 0, top: 0, width: 320, height: 240 }) as DOMRect;
  notify([], {} as ResizeObserver);
  expect(view.container.querySelector<HTMLElement>(".interaction-marker")?.style.left).toBe("80px");
  view.handle.destroy();
  expect(disconnect).toHaveBeenCalledTimes(1);
  view.handle.setVideo("other.webm");
  notify([], {} as ResizeObserver);
  expect(view.frames.size).toBe(0);
});
it("reports baked live indicators and a timed terminal truncation row", async () => {
  const data = rows.map((r) => (r.type === "interaction" ? { ...r, shown: true } : r));
  const view = setup(
    vi.fn(async () => reply([...data, { type: "truncated", v: 1, at: 1000000000400, reason: "capacity" }])),
  );
  await flush();
  view.video.currentTime = 0.2;
  view.checkbox.checked = true;
  view.checkbox.dispatchEvent(new Event("change"));
  expect(view.container.querySelector(".interaction-marker")).toBeNull();
  expect(view.container.textContent).toContain("already in the video");
  expect(view.container.textContent).toContain("truncated");
});

it("expands release rings by video-clock age rather than a wall-clock animation", async () => {
  const event = rows[1]!;
  const view = setup(
    vi.fn(async () =>
      reply([
        ...rows,
        { ...event, type: "interaction", phase: "up", t: 300, at: 1000000000300 } as InteractionSidecarRow,
      ]),
    ),
  );
  await flush();
  view.checkbox.checked = true;
  view.video.currentTime = 0.51;
  view.checkbox.dispatchEvent(new Event("change"));
  const marker = view.container.querySelector<HTMLElement>(".interaction-marker");
  expect(marker?.style.opacity).toBe("0.5");
  expect(marker?.style.transform).toBe("translate(-50%, -50%) scale(1.3)");
});
