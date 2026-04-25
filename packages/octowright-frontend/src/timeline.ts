import { colorForAction, eventHeadline, formatTime, relativeSeconds } from "./format.js";
import type { RecordingEvent } from "./types.js";

export interface TimelineOptions {
  /** Called when an event row is clicked. Argument is seconds offset from first event. */
  onSeek?: (seconds: number, event: RecordingEvent) => void;
}

export function renderTimeline(container: HTMLElement, events: RecordingEvent[], opts: TimelineOptions = {}): void {
  container.innerHTML = "";
  container.classList.add("timeline");
  if (events.length === 0) {
    const empty = document.createElement("div");
    empty.className = "timeline__empty";
    empty.textContent = "No actions recorded yet.";
    container.append(empty);
    return;
  }
  const first = events[0];
  if (!first) return;
  const baseIso = first.ts;
  const list = document.createElement("ol");
  list.className = "timeline__list";
  for (const event of events) {
    list.append(renderRow(event, baseIso, opts));
  }
  container.append(list);
}

export function appendTimelineEvents(
  container: HTMLElement,
  newEvents: RecordingEvent[],
  baseIso: string,
  opts: TimelineOptions = {},
): void {
  if (newEvents.length === 0) return;
  let list = container.querySelector<HTMLOListElement>("ol.timeline__list");
  if (!list) {
    container.innerHTML = "";
    list = document.createElement("ol");
    list.className = "timeline__list";
    container.append(list);
  }
  for (const event of newEvents) {
    list.append(renderRow(event, baseIso, opts));
  }
}

function renderRow(event: RecordingEvent, baseIso: string, opts: TimelineOptions): HTMLLIElement {
  const li = document.createElement("li");
  li.className = `timeline__row timeline__row--${colorForAction(event.action)}`;
  li.setAttribute("data-action", event.action);
  li.setAttribute("data-ts", event.ts);

  const tsBtn = document.createElement("button");
  tsBtn.type = "button";
  tsBtn.className = "timeline__ts";
  tsBtn.textContent = `[${formatTime(event.ts)}]`;
  const seconds = relativeSeconds(event.ts, baseIso);
  tsBtn.setAttribute("data-seconds", String(seconds));
  if (opts.onSeek) {
    tsBtn.addEventListener("click", () => opts.onSeek?.(seconds, event));
  }

  const action = document.createElement("span");
  action.className = "timeline__action";
  action.textContent = event.action;

  const headline = document.createElement("span");
  headline.className = "timeline__headline";
  headline.textContent = eventHeadline(event);

  li.append(tsBtn, action, headline);
  return li;
}
