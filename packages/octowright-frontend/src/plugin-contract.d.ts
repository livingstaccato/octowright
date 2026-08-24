// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * The contract a session-kind plugin's dashboard renderer implements.
 *
 * Core owns the page chrome, the WebSocket, the cursor protocol and the auth
 * notice. A plugin implements exactly one function and receives batches.
 */

/**
 * One recorded JSONL row. Kind-specific fields ride along untyped.
 *
 * Structurally identical to core's internal `RecordingEvent` (`types.ts`), and
 * declared separately on purpose: this file is the PUBLISHED contract, and a
 * third party should not have to import core's internal types to build against
 * it. TypeScript is structural, so core passes these to `renderTimeline` with
 * no conversion.
 *
 * Two identical interfaces can silently diverge, so a test pins their
 * compatibility (Task 3, step 2) rather than trusting the comment.
 */
export interface SessionEvent {
  ts: string;
  action: string;
  [field: string]: unknown;
}

/** What core hands a renderer at mount time. */
export interface StreamContext {
  /** The session this pane renders. */
  sessionId: string;
  /** Whether the session is still live; a closed session receives history only. */
  live: boolean;
  /** The kind that selected this renderer. */
  kind: string;
}

export interface StreamHandle {
  /**
   * Receive a batch of events in JSONL order.
   *
   * Historical events are fed before any live event. Delivery is
   * AT-LEAST-ONCE: a `/tail` reconnect may replay a batch, so a renderer must
   * tolerate a repeat.
   */
  feed(events: SessionEvent[]): void;

  /** Idempotent. Core always calls it on teardown once a handle exists. */
  destroy(): void;
}

/**
 * May be async; core awaits it before the first `feed`.
 *
 * Throwing (or rejecting) yields no handle, so there is nothing to destroy —
 * core switches the pane to its fallback renderer with a visible reason.
 */
export type MountStream = (el: HTMLElement, ctx: StreamContext) => StreamHandle | Promise<StreamHandle>;
