import { describe, expect, it } from "vitest";
import { InteractionPlayback, mapInteractionPoint, videoBasename } from "./interaction-playback.js";
import type { InteractionRow, InteractionSidecarRow, VideoGeometry } from "./types.js";

const geometry: VideoGeometry = {
  engine: "chromium",
  viewport_width: 640,
  viewport_height: 480,
  device_scale_factor: 2,
};
const page = (page_id = 1, video: string | null = "one.webm", origin = 1000000000000) =>
  ({
    type: "page",
    v: 1,
    page_id,
    video,
    time_origin: origin,
    time_origin_kind: "estimate",
    video_geometry: geometry,
  }) as const;
const input = (
  t: number,
  phase: InteractionRow["phase"] = "down",
  extra: Partial<InteractionRow> = {},
): InteractionRow => ({
  type: "interaction",
  v: 1,
  page_id: 1,
  video: null,
  t,
  at: 1000000000000 + t,
  phase,
  pointer_type: "mouse",
  pointer_id: 1,
  button: 0,
  x: 160,
  y: 240,
  width: 640,
  height: 480,
  shown: false,
  ...extra,
});

describe("native canvas then CSS contain mapping", () => {
  it.each([
    ["chromium", 160, 120, 40, 60, 40, 60],
    ["chromium", 640, 240, 160, 120, 80, 60],
    ["chromium", 160, 480, 40, 240, 20, 120],
    ["webkit", 160, 120, 40, 60, 80, 120],
    ["firefox", 640, 480, 160, 240, 80, 120],
  ])("maps %s viewport %sx%s", (engine, width, height, x, y, ex, ey) => {
    expect(
      mapInteractionPoint(
        input(0, "down", { width, height, x, y }),
        { ...geometry, engine } as VideoGeometry,
        320,
        240,
        { left: 0, top: 0, width: 320, height: 240 },
      ),
    ).toEqual({ x: ex, y: ey });
  });
  it("centers encoded canvas in min-height and wide letterboxes", () => {
    expect(mapInteractionPoint(input(0), geometry, 320, 240, { left: 0, top: 0, width: 200, height: 240 })).toEqual({
      x: 50,
      y: 120,
    });
    expect(mapInteractionPoint(input(0), geometry, 320, 240, { left: 5, top: 2, width: 800, height: 240 })).toEqual({
      x: 325,
      y: 122,
    });
  });
  it("refuses unknown geometry, unloaded video and Firefox resize", () => {
    expect(mapInteractionPoint(input(0), null, 320, 240, { left: 0, top: 0, width: 320, height: 240 })).toBeNull();
    expect(mapInteractionPoint(input(0), geometry, 0, 0, { left: 0, top: 0, width: 320, height: 240 })).toBeNull();
    expect(
      mapInteractionPoint(input(0, "down", { width: 160 }), { ...geometry, engine: "firefox" }, 320, 240, {
        left: 0,
        top: 0,
        width: 320,
        height: 240,
      }),
    ).toBeNull();
  });
});
describe("indexed video clock", () => {
  it("joins late page identities, filters videos and normalizes Windows names", () => {
    const rows: InteractionSidecarRow[] = [input(10), input(20, "down", { page_id: 2 }), page(), page(2, "two.webm")];
    const model = new InteractionPlayback(rows, "C:\\captures\\one.webm");
    expect(videoBasename("/a/one.webm")).toBe("one.webm");
    expect(model.at(20).map((m) => m.row.page_id)).toEqual([1]);
    model.setVideo("two.webm");
    expect(model.at(20).map((m) => m.row.page_id)).toEqual([2]);
    model.setVideo(null);
    expect(model.at(20)).toEqual([]);
  });
  it("holds after 500ms, seeks backward, drags, releases for 420ms and cancels", () => {
    const model = new InteractionPlayback(
      [page(), input(100), input(800, "move"), input(900, "up"), input(1500), input(1600, "cancel")],
      "one.webm",
    );
    expect(model.at(599)[0]?.label).toBe("Click");
    expect(model.at(600)[0]?.label).toBe("Hold");
    expect(model.at(850)[0]?.label).toBe("Drag");
    expect(model.at(950)[0]?.phase).toBe("up");
    expect(model.at(1320)).toEqual([]);
    expect(model.at(1600)).toEqual([]);
    expect(model.at(600)[0]?.progress).toBe(1);
    expect(model.at(99)).toEqual([]);
  });
  it("processes baked-in ends and replaces the previous pulse on double", () => {
    const model = new InteractionPlayback(
      [
        page(),
        input(0),
        input(50, "up"),
        input(100),
        input(150, "up"),
        input(150, "double"),
        input(700),
        input(750, "up", { shown: true }),
      ],
      "one.webm",
    );
    expect(model.at(150).map((m) => m.label)).toEqual(["Double click"]);
    expect(model.at(750)).toEqual([]);
  });
  it("keeps contacts distinct and maps global/page controls through immutable origins", () => {
    const rows: InteractionSidecarRow[] = [
      page(),
      page(2, "one.webm", 1000000001000),
      input(0),
      input(0, "down", { page_id: 2, pointer_type: "touch" }),
      { type: "reset", v: 1, at: 1000000000100, reset: true, reason: "page_closed", page_id: 1 },
    ];
    const model = new InteractionPlayback(rows, "one.webm");
    expect(model.at(100).map((m) => m.row.page_id)).toEqual([2]);
    rows.push({ type: "settings", v: 1, at: 1000000001200, show: false, record: false, reset: true });
    expect(new InteractionPlayback(rows, "one.webm").at(200)).toEqual([]);
  });
  it("rebuilds beyond checkpoints and clears contacts on truncation/loss", () => {
    const rows: InteractionSidecarRow[] = [
      page(),
      input(0),
      ...Array.from({ length: 600 }, (_, i) => input(i + 1, "move")),
    ];
    rows.push({ type: "truncated", v: 1, at: 1000000000700, reason: "capacity" });
    const model = new InteractionPlayback(rows, "one.webm");
    expect(model.at(650)).toHaveLength(1);
    expect(model.at(700)).toEqual([]);
    expect(model.at(500)).toHaveLength(1);
  });
});

it("retains separate release/wheel pulses without ending a live press and bounds pulses", () => {
  const rows: InteractionSidecarRow[] = [
    page(),
    input(0, "down", { pointer_id: 0 }),
    ...Array.from({ length: 60 }, (_, i) => input(i + 1, "wheel", { pointer_id: 0, dy: 1 })),
  ];
  const model = new InteractionPlayback(rows, "one.webm");
  expect(model.at(60)).toHaveLength(33);
  expect(model.at(500).map((m) => m.label)).toEqual(["Hold"]);
});

it("honors native double phases outside the observer's inferred interval", () => {
  const model = new InteractionPlayback(
    [page(), input(0), input(10, "up"), input(350), input(360, "up"), input(361, "double")],
    "one.webm",
  );
  expect(model.at(361).map((m) => m.label)).toEqual(["Double click"]);
});
it("retains the first page clock when later page metadata supplies a different origin", () => {
  const model = new InteractionPlayback(
    [
      page(),
      input(0),
      page(1, "one.webm", 1000000001000),
      { type: "reset", v: 1, at: 1000000000500, reset: true, reason: "loss" },
    ],
    "one.webm",
  );
  expect(model.at(499)).toHaveLength(1);
  expect(model.at(500)).toEqual([]);
});

it.each([
  [0, 0, "Wheel ↓"],
  [0, -1, "Wheel ↑"],
  [1, 0, "Wheel →"],
  [-1, 0, "Wheel ←"],
])("labels wheel direction %s,%s", (dx, dy, expected) => {
  const model = new InteractionPlayback([page(), input(0, "wheel", { dx, dy })], "one.webm");
  expect(model.at(0)[0]?.label).toBe(expected);
});
it.each([
  ["mouse", 1, "Middle click"],
  ["mouse", 2, "Right click"],
  ["pen", 0, "Pen"],
  ["touch", 0, "Touch 1"],
] as const)("labels %s button %s", (pointer_type, button, expected) => {
  expect(
    new InteractionPlayback([page(), input(0, "down", { pointer_type, button })], "one.webm").at(0)[0]?.label,
  ).toBe(expected);
});
it("labels touch doubles and caps simultaneous contacts at sixteen", () => {
  expect(
    new InteractionPlayback([page(), input(0, "double", { pointer_type: "touch" })], "one.webm").at(0)[0]?.label,
  ).toBe("Double tap · Touch 1");
  expect(
    new InteractionPlayback(
      [
        page(),
        ...Array.from({ length: 20 }, (_, pointer_id) => input(0, "down", { pointer_id, pointer_type: "touch" })),
      ],
      "one.webm",
    ).at(0),
  ).toHaveLength(16);
});
it("maps Firefox's initial small viewport without an upscale", () => {
  expect(
    mapInteractionPoint(
      input(0, "down", { width: 160, height: 120, x: 40, y: 60 }),
      { ...geometry, engine: "firefox", viewport_width: 160, viewport_height: 120 },
      320,
      240,
      { left: 0, top: 0, width: 320, height: 240 },
    ),
  ).toEqual({ x: 40, y: 60 });
});

it("preserves drag and hold contact labels through the 420ms release", () => {
  const model = new InteractionPlayback(
    [page(), input(0), input(600, "up"), input(1100), input(1200, "move"), input(1300, "up")],
    "one.webm",
  );
  expect(model.at(650)[0]?.label).toBe("Hold");
  expect(model.at(1350)[0]?.label).toBe("Drag");
});

it("does not paint an interaction explicitly associated with a different native video", () => {
  const model = new InteractionPlayback([page(), input(0, "down", { video: "other.webm" })], "one.webm");
  expect(model.at(0)).toEqual([]);
});
