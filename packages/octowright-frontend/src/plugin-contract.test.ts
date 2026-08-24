import { describe, expectTypeOf, it } from "vitest";

import type { SessionEvent } from "./plugin-contract.js";
import type { RecordingEvent } from "./types.js";

describe("published contract", () => {
  it("SessionEvent stays assignable to core's RecordingEvent in both directions", () => {
    // The published type is declared standalone so a third party need not
    // import core internals -- but core feeds these straight into
    // renderTimeline, which takes RecordingEvent. If the two ever diverge,
    // this fails here rather than in someone else's plugin.
    expectTypeOf<SessionEvent>().toMatchTypeOf<RecordingEvent>();
    expectTypeOf<RecordingEvent>().toMatchTypeOf<SessionEvent>();
  });
});
