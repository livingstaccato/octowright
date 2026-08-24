// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * The reference plugin's dashboard renderer.
 *
 * Deliberately the smallest real consumer of `plugin-contract.d.ts`: it
 * exists to prove mount -> feed -> destroy end to end without pulling in a
 * UI library, so a drift in the published contract fails CI here rather than
 * in a third party's project months later. Its only behavior is appending
 * each fed event's `action` as one line of text to the mounted element.
 *
 * Honors the contract's three rules as a plain function rather than a class:
 * mount may be async (this one isn't, and returning a bare object instead of
 * a Promise is valid per `StreamHandle | Promise<StreamHandle>`); historical
 * events arrive in the same `feed` stream as live ones, in order, so no
 * separate "seed" path is needed; and delivery is at-least-once, so `feed`
 * does no dedup -- a replayed batch just appends its lines again, which is
 * the honest behavior for a renderer this minimal.
 */

export function mountStream(el, ctx) {
  const log = document.createElement("div");
  log.dataset.refkindStream = ctx.sessionId;
  el.appendChild(log);

  return {
    feed(events) {
      for (const event of events) {
        const line = document.createElement("div");
        line.textContent = event.action;
        log.appendChild(line);
      }
    },
    destroy() {
      log.remove();
    },
  };
}
