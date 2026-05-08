# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Headed walkthrough of the macro status pill.

Launches a real Chromium window, runs the `pill-status-demo` macro a few
times with slowmo so each step is easy to follow by eye, then parks until
Ctrl-C. The bottom-center pill should show:

    [pillproof]  0.8s  ·  pill-status-demo | evaluate
     ^^^^^^^^^^   ^^^     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
     id chip    elapsed   action description

The corner badge in the bottom-right shares a color with the chip, seeded
from the launch label.

Usage::

    # Point octowright at the example macros so it can find pill-status-demo.
    export OCTOWRIGHT_MACROS_DIR=examples/macros
    uv run python examples/pill-status-demo/run.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from octowright.browser_pool import BrowserPool
from octowright.macros.execution import run_macro

# Per-action delay applied via run_macro(slowmo_ms=...). 1.2s is slow enough
# to read the action description and watch the elapsed counter advance, but
# not so slow that the demo drags.
SLOWMO_MS = 1200

# Number of times to play the macro back-to-back. Multiple iterations make
# the elapsed-timer reset behaviour visible (each run_macro pushes a fresh
# `start: true` and the counter goes back to 0.0s).
ITERATIONS = 3

PROBE_HTML = Path(__file__).resolve().parent / "probe.html"


async def main() -> int:
    if not PROBE_HTML.exists():
        sys.exit(f"probe page missing: {PROBE_HTML}")

    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url=PROBE_HTML.as_uri(),
            headed=True,
            label="pillproof",
            viewport_w=1100,
            viewport_h=720,
        )
        session = pool.get(result["instance_id"])

        print(f"\nbrowser launched — instance {result['instance_id']}")
        print(f"watch the bottom-center pill. running pill-status-demo at slowmo={SLOWMO_MS}ms…\n")

        for i in range(1, ITERATIONS + 1):
            print(f"  iteration {i}/{ITERATIONS} …")
            outcome = await run_macro(session, "pill-status-demo", slowmo_ms=SLOWMO_MS)
            print(f"    -> executed={outcome['executed']}, slowmo_ms={outcome['slowmo_ms']}")
            # Pause between runs so the hide animation is visible and the
            # elapsed counter clearly resets on the next start.
            await asyncio.sleep(2.0)

        print("\nMacros done. Browser stays open. Press Ctrl-C in this terminal to close.")
        await asyncio.Event().wait()  # park until interrupted
        return 0
    finally:
        await pool.shutdown()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nclosed.")
        sys.exit(0)
