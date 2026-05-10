#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Headed integration demo for the new `wait_for(expression=...)` branch.

Launches a real Chromium browser (headed by default — pass --headless to
override) on a synthetic page that:

  1. Renders a loading spinner with id="spinner".
  2. After ~500ms removes the spinner.
  3. After ~1500ms appends 3 <tr> rows to a <tbody>.

The compound condition the LLM-side caller wants to wait on is:

    !document.querySelector('#spinner') && document.querySelectorAll('tbody tr').length > 0

That state is only true after step 3. A naive `wait_for(selector="#spinner",
present=False)` would only wait for step 2 and miss step 3. A naive
`wait_for(selector="tbody tr")` would fire on the first row (race with
the spinner removal). The compound JS predicate captures both.

The demo prints timing for:
  - When step 2 fires (spinner removed)
  - When step 3 fires (rows populated)
  - When wait_for_expression() returns

A successful run shows wait_for_expression() returning between 1500ms and
~2500ms after navigation — i.e. it correctly held until BOTH conditions
were met, not earlier.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from octowright.browser_pool import BrowserPool

DEMO_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>wait_for(expression=) demo</title>
<style>
  body { font: 16px/1.4 system-ui, sans-serif; padding: 24px; }
  #spinner { display: inline-block; width: 18px; height: 18px;
             border: 3px solid #ccc; border-top-color: #07c;
             border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  table { border-collapse: collapse; margin-top: 16px; min-width: 320px; }
  th, td { border: 1px solid #ccc; padding: 6px 12px; text-align: left; }
  th { background: #f0f0f0; }
  .ts { font-family: monospace; color: #888; }
</style></head><body>
<h1>wait_for(expression=) demo</h1>
<p>Loading … <span id="spinner"></span></p>
<table><thead><tr><th>id</th><th>name</th><th>state</th></tr></thead>
<tbody id="rows"></tbody></table>
<p class="ts">step log will appear here</p>
<script>
  const log = document.querySelector('.ts');
  const stamp = () => `${(performance.now() / 1000).toFixed(3)}s`;
  log.textContent = `t=${stamp()} navigated`;

  setTimeout(() => {
    document.querySelector('#spinner').remove();
    log.textContent += `\\nt=${stamp()} step 2: spinner removed`;
  }, 500);

  setTimeout(() => {
    const tbody = document.querySelector('#rows');
    for (let i = 1; i <= 3; i++) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${i}</td><td>row ${i}</td><td>ready</td>`;
      tbody.appendChild(tr);
    }
    log.textContent += `\\nt=${stamp()} step 3: ${tbody.children.length} rows populated`;
  }, 1500);
</script>
</body></html>
"""

PREDICATE = "!document.querySelector('#spinner') && document.querySelectorAll('tbody tr').length > 0"


async def run(headed: bool, hold_seconds: float) -> int:
    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url="about:blank",
            headed=headed,
            label="wait-for-expression-demo",
            viewport_w=900,
            viewport_h=600,
        )
        iid = result["instance_id"]
        session = pool.get(iid)

        # Inject the demo HTML directly so we don't need a tmp HTTP server.
        await session.page.set_content(DEMO_HTML)
        nav_start = time.perf_counter()
        print(f"[t=+0.000s] page loaded — calling wait_for(expression={PREDICATE!r})")

        # The actual feature exercise: wait until BOTH the spinner is gone
        # AND tbody has rows. Should resolve ~1.5s in, not 0.5s.
        await session.wait_for(None, None, 5_000, expression=PREDICATE)
        elapsed = time.perf_counter() - nav_start
        print(f"[t=+{elapsed:.3f}s] wait_for(expression=) RETURNED")

        # Sanity-check the page state at return time so the demo proves
        # the predicate really was true (not a Playwright-side timeout
        # that returned silently).
        spinner_present = await session.page.evaluate("!!document.querySelector('#spinner')")
        row_count = await session.page.evaluate("document.querySelectorAll('tbody tr').length")
        print(f"           spinner_present={spinner_present}  row_count={row_count}")
        if spinner_present or row_count == 0:
            print("FAIL: predicate returned but page state didn't actually satisfy it")
            return 2
        if elapsed < 1.4:
            print(
                f"FAIL: returned at t=+{elapsed:.3f}s — too early; the "
                "spinner-removed signal at t=0.5 should NOT have unblocked "
                "this wait. Did the predicate get OR'd instead of AND'd?"
            )
            return 2
        print("PASS: predicate held until both conditions were true (≥ 1.4s)")

        if hold_seconds > 0:
            print(f"holding browser open for {hold_seconds}s so you can see the page...")
            await asyncio.sleep(hold_seconds)

        await pool.close(iid)
        return 0
    finally:
        await pool.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run headless (default: headed so you can watch the page).",
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=2.0,
        help="seconds to hold the browser open after the wait returns (default: 2).",
    )
    args = parser.parse_args()
    return asyncio.run(run(headed=not args.headless, hold_seconds=args.hold))


if __name__ == "__main__":
    raise SystemExit(main())
