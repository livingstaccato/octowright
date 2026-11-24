import type { InteractionPageRow, InteractionRow, InteractionSidecarRow, VideoGeometry } from "./types.js";

export function videoBasename(path: string | null): string | null {
  return path?.replace(/\\/g, "/").split("/").pop() || null;
}
export interface VideoBox {
  left: number;
  top: number;
  width: number;
  height: number;
}
export function fittedCanvas(nw: number, nh: number, box: VideoBox): VideoBox | null {
  if (![nw, nh, box.width, box.height].every((n) => Number.isFinite(n) && n > 0)) return null;
  const scale = Math.min(box.width / nw, box.height / nh);
  return {
    left: box.left + (box.width - nw * scale) / 2,
    top: box.top + (box.height - nh * scale) / 2,
    width: nw * scale,
    height: nh * scale,
  };
}
export function supportedGeometry(row: InteractionRow, geometry: VideoGeometry | null): boolean {
  return (
    geometry !== null &&
    (geometry.engine !== "firefox" ||
      (row.width === geometry.viewport_width && row.height === geometry.viewport_height))
  );
}
export function mapInteractionPoint(
  row: InteractionRow,
  geometry: VideoGeometry | null,
  nw: number,
  nh: number,
  box: VideoBox,
): { x: number; y: number } | null {
  const canvas = fittedCanvas(nw, nh, box);
  if (!canvas || !geometry || !supportedGeometry(row, geometry)) return null;
  const nativeScale = Math.min(
    geometry.engine === "webkit" ? geometry.device_scale_factor : 1,
    nw / row.width,
    nh / row.height,
  );
  return {
    x: canvas.left + (row.x * nativeScale * canvas.width) / nw,
    y: canvas.top + (row.y * nativeScale * canvas.height) / nh,
  };
}
interface Contact {
  row: InteractionRow;
  start: number;
  dragged: boolean;
  end: number | null;
}
interface TimedRow {
  t: number;
  order: number;
  row: InteractionRow | null;
  page: number;
}
export interface InteractionMarker {
  row: InteractionRow;
  geometry: VideoGeometry | null;
  phase: string;
  label: string;
  progress: number;
  opacity: number;
}
const key = (r: InteractionRow): string => `${r.page_id}:${r.pointer_type}:${r.pointer_id}`;
function label(row: InteractionRow): string {
  if (row.phase === "wheel") {
    const dx = row.dx ?? 0,
      dy = row.dy ?? 0;
    return `Wheel ${Math.abs(dy) >= Math.abs(dx) ? (dy >= 0 ? "↓" : "↑") : dx > 0 ? "→" : "←"}`;
  }
  const base =
    row.pointer_type === "touch"
      ? `Touch ${row.pointer_id}`
      : row.pointer_type === "pen"
        ? "Pen"
        : row.button === 2
          ? "Right click"
          : row.button === 1
            ? "Middle click"
            : "Click";
  return row.phase === "double" ? (row.pointer_type === "touch" ? `Double tap · ${base}` : "Double click") : base;
}
function apply(state: Map<string, Contact>, entry: TimedRow): void {
  for (const [id, c] of state)
    if ((c.end !== null && entry.t >= c.end + 420) || (!entry.row && c.row.page_id === entry.page)) state.delete(id);
  const row = entry.row;
  if (!row) return;
  const id = key(row),
    previous = state.get(id);
  if (row.phase === "cancel") {
    state.delete(id);
    return;
  }
  if (row.phase === "double") {
    const releases = [...state].filter(
      ([, c]) =>
        c.end !== null &&
        c.row.phase !== "wheel" &&
        c.row.page_id === row.page_id &&
        c.row.pointer_type === row.pointer_type &&
        c.row.button === row.button,
    );
    // The producer already classified the double, including the native dblclick fallback.
    for (const [other] of releases.slice(-2)) state.delete(other);
  }
  if (row.phase === "move") {
    if (previous && previous.end === null) state.set(id, { ...previous, row, dragged: true });
    return;
  }
  if (row.phase === "down") {
    const active = [...state.values()].filter((c) => c.end === null).length;
    if (previous || active < 16) state.set(id, { row, start: entry.t, dragged: false, end: null });
    return;
  }
  if (row.phase !== "wheel") state.delete(id);
  state.set(`pulse:${entry.order}:${entry.page}`, {
    row,
    start: row.phase === "wheel" ? entry.t : (previous?.start ?? entry.t),
    dragged: row.phase !== "wheel" && (previous?.dragged ?? false),
    end: entry.t,
  });
  const pulses = [...state].filter(([, c]) => c.end !== null);
  for (const [old] of pulses.slice(0, Math.max(0, pulses.length - 32))) state.delete(old);
}

/** Sorted once; checkpoints bound backward-seek replay to 255 observations. */
export class InteractionPlayback {
  private pages = new Map<number, InteractionPageRow>();
  private entries: TimedRow[] = [];
  private checkpoints: Map<string, Contact>[] = [];
  private state = new Map<string, Contact>();
  private cursor = 0;
  private lastTime = -1;
  hasEvents = false;
  hasBakedMarkers = false;
  constructor(
    private rows: InteractionSidecarRow[],
    selected: string | null,
  ) {
    this.setVideo(selected);
  }
  setVideo(selected: string | null): void {
    this.pages.clear();
    this.entries = [];
    this.checkpoints = [];
    this.state.clear();
    this.cursor = 0;
    this.lastTime = -1;
    const basename = videoBasename(selected);
    for (const row of this.rows)
      if (row.type === "page") {
        const first = this.pages.get(row.page_id);
        this.pages.set(row.page_id, { ...row, time_origin: first?.time_origin ?? row.time_origin });
      }
    const selectedPages = new Map(
      [...this.pages].filter(([, page]) => basename !== null && videoBasename(page.video) === basename),
    );
    for (const [order, row] of this.rows.entries()) {
      if (row.type === "page") continue;
      if (row.type === "interaction") {
        if (selectedPages.has(row.page_id) && (row.video === null || videoBasename(row.video) === basename)) {
          this.entries.push({ t: row.t, order, row, page: row.page_id });
        }
        continue;
      }
      for (const page of selectedPages.values()) {
        if (row.type !== "reset" || row.reason !== "page_closed" || row.page_id === page.page_id) {
          this.entries.push({ t: Math.max(0, row.at - page.time_origin), order, row: null, page: page.page_id });
        }
      }
    }
    this.entries.sort((a, b) => a.t - b.t || a.order - b.order);
    this.hasEvents = this.entries.some((e) => e.row !== null);
    this.hasBakedMarkers = this.entries.some((e) => e.row?.shown);
    const state = new Map<string, Contact>();
    for (let i = 0; i <= this.entries.length; i++) {
      if (i % 256 === 0) this.checkpoints.push(new Map(state));
      const entry = this.entries[i];
      if (entry) apply(state, entry);
    }
  }
  at(t: number): InteractionMarker[] {
    let lo = 0,
      hi = this.entries.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if ((this.entries[mid]?.t ?? Infinity) <= t) lo = mid + 1;
      else hi = mid;
    }
    if (t < this.lastTime || lo - this.cursor > 256) {
      this.cursor = Math.floor(lo / 256) * 256;
      this.state = new Map(this.checkpoints[this.cursor / 256]);
    }
    while (this.cursor < this.entries.length) {
      const entry = this.entries[this.cursor];
      if (!entry || entry.t > t) break;
      apply(this.state, entry);
      this.cursor++;
    }
    this.lastTime = t;
    const markers: InteractionMarker[] = [];
    for (const c of this.state.values()) {
      if (c.row.shown || (c.end !== null && t >= c.end + 420)) continue;
      const phase = c.end !== null ? c.row.phase : c.dragged ? "drag" : t - c.start >= 500 ? "hold" : "down";
      const contactPhase =
        c.row.phase === "up" ? (c.dragged ? "drag" : (c.end ?? t) - c.start >= 500 ? "hold" : "down") : phase;
      markers.push({
        row: c.row,
        geometry: this.pages.get(c.row.page_id)?.video_geometry ?? null,
        phase,
        label:
          contactPhase === "hold"
            ? `Hold${c.row.pointer_type === "touch" ? ` · Touch ${c.row.pointer_id}` : ""}`
            : contactPhase === "drag"
              ? `Drag${c.row.pointer_type === "touch" ? ` · Touch ${c.row.pointer_id}` : ""}`
              : label(c.row),
        progress: Math.min(1, Math.max(0, (t - c.start) / 500)),
        opacity: c.end === null ? 1 : Math.max(0, 1 - (t - c.end) / 420),
      });
    }
    return markers;
  }
}
