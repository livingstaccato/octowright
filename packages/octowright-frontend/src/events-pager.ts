import { getEvents } from "./api.js";
import type { EventsResponse, RecordingEvent } from "./types.js";

/**
 * Every recorded event from `since` on, following `/events` pages to the end.
 *
 * The server bounds one `/events` answer (8 MiB of JSONL by default) and says
 * so with `complete: false`; the session pages fetched once and rendered that
 * first page as the whole history, so a large closed recording showed a
 * timeline that stopped partway with nothing saying it had.
 *
 * Stops at `complete`, or when a page does not move the cursor: a live
 * recording's trailing line that is still being written holds the cursor
 * where it is, and the tail picks it up from there. A page that does not
 * advance contributes nothing, so the returned cursor is the furthest one the
 * server reported.
 */
export async function getAllEvents(id: string, since = 0): Promise<EventsResponse> {
  const events: RecordingEvent[] = [];
  let from = since;
  let result: EventsResponse | null = null;
  for (;;) {
    const page = await getEvents(id, from);
    const progressed = page.cursor > from;
    if (result === null || progressed) {
      // A loop, not push(...page.events): spreading a page of tens of
      // thousands of events overflows the call stack.
      for (const event of page.events) events.push(event);
      result = page;
    }
    if (page.complete || !progressed) break;
    from = page.cursor;
  }
  return { ...result, events };
}
