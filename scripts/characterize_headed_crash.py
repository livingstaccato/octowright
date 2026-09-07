# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Reproduce (and A/B) the recurring headed-Chromium browser-process abort.

Not shipped in the wheel. It lives in the repo because the previous version of
this harness lived in a gitignored ``scratchpad/`` and was wiped, which is the
only reason the question it answers is still open.

**The bug.** Headed Chromium dies as a whole browser -- ``EXC_BREAKPOINT`` on
``CrBrowserMain``, a deliberate CHECK/``ImmediateCrash`` abort, so crash
recovery (which replaces a dead *renderer*) is 0-for-N. A controlled matrix on
2026-07-17 (Chrome 148, macOS 26, Apple Silicon) established the trigger is
**window creation under concurrency**, not the sites and not memory::

    headed  + churn  + 8   ->  195 crashes / 12 min
    headless+ churn  + 8   ->    0            (the windowing path is required)
    headed  + static + 8   ->    1            (navigation is not the trigger)
    headed  + churn  + 3   ->    0            (it scales with concurrency)

**What is still unknown**, and what this script exists to answer: whether
``OCTOWRIGHT_DISABLE_GPU`` prevents it. That knob shipped as an escape hatch and
AGENTS.md is explicit that it is *not* a proven fix. It did not exist when the
matrix above was run, so nothing has ever tested it.

**Method, and why it is not a simple before/after.** The crash is bursty rather
than steady: re-reading the 2026-07-17..21 monitor data in 2026-09-03 showed 251
of 254 events inside a single ~2-hour window, with two quiet days on either side
in *both* arms. Running arm A for ten minutes and then arm B for ten minutes
therefore measures which arm happened to own the burst. So the arms are
**interleaved** in short blocks and the block order alternates each round, and
the reported figure is per-block episode counts rather than one total.

A crash is counted from the browser's own ``disconnected`` event while we still
expect it to be alive -- immediate, attributable to the block that caused it, and
independent of crashpad's pruning. Fresh macOS ``.ips`` reports are counted
alongside as corroboration only.

Usage::

    uv run --active python scripts/characterize_headed_crash.py --rounds 1 --arms gpu-on
    uv run --active python scripts/characterize_headed_crash.py --rounds 6 --block-seconds 90
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

# Matches the shipped knob's flag set exactly; testing a different one would
# answer a question nobody can act on.
GPU_DISABLE_ARGS = ("--disable-gpu", "--disable-gpu-compositing")

# 8 reproduced it and 3 did not, so the storm arm uses 8.
DEFAULT_BROWSERS = 8
DEFAULT_BLOCK_SECONDS = 180.0
DEFAULT_ROUNDS = 1

_IPS_DIR = Path.home() / "Library" / "Logs" / "DiagnosticReports"


@dataclass
class BlockResult:
    arm: str
    seconds: float
    cycles: int
    launched: int
    crashes: int
    ips_new: int
    errors: list[str] = field(default_factory=list)


def _ips_names() -> set[str]:
    """Chrome-ish crash reports currently on disk, by name.

    By NAME rather than count: macOS prunes this directory, so a count can fall
    while new reports arrive.
    """
    try:
        return {p.name for p in _IPS_DIR.glob("*.ips") if "hrome" in p.name}
    except OSError:
        return set()


async def _one_cycle(browser_type: Any, args: list[str], count: int, state: dict[str, int]) -> list[str]:
    """Launch *count* headed browsers at once, open a page in each, close them.

    Concurrent creation is the whole point -- this deliberately does NOT use the
    semaphore octowright's own roster applies, because the storm is the thing
    under test.
    """
    errors: list[str] = []
    browsers: list[Any] = []

    async def _launch() -> Any:
        browser = await browser_type.launch(headless=False, args=args)
        # A death while we still expect it alive is the signal. Playwright fires
        # this for an ordinary close too, so the flag below gates it.
        browser.on("disconnected", lambda _b=browser: _note_disconnect(_b, state))
        browser._expected_close = False
        await (await browser.new_page()).goto("about:blank")
        return browser

    results = await asyncio.gather(*(_launch() for _ in range(count)), return_exceptions=True)
    for item in results:
        if isinstance(item, BaseException):
            errors.append(f"{type(item).__name__}: {item}"[:160])
            state["crashes"] += 1
        else:
            browsers.append(item)
            state["launched"] += 1

    for browser in browsers:
        browser._expected_close = True
        try:
            await browser.close()
        except Exception as exc:
            errors.append(f"close {type(exc).__name__}: {exc}"[:160])
    return errors


def _note_disconnect(browser: Any, state: dict[str, int]) -> None:
    if not getattr(browser, "_expected_close", False):
        state["crashes"] += 1


async def _run_block(browser_type: Any, arm: str, seconds: float, count: int) -> BlockResult:
    args = list(GPU_DISABLE_ARGS) if arm == "gpu-off" else []
    before = _ips_names()
    state = {"crashes": 0, "launched": 0}
    errors: list[str] = []
    cycles = 0
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        errors.extend(await _one_cycle(browser_type, args, count, state))
        cycles += 1
    # Reports are written a beat after the process dies.
    await asyncio.sleep(3)
    return BlockResult(
        arm=arm,
        seconds=round(time.monotonic() - started, 1),
        cycles=cycles,
        launched=state["launched"],
        crashes=state["crashes"],
        ips_new=len(_ips_names() - before),
        errors=errors[:10],
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--block-seconds", type=float, default=DEFAULT_BLOCK_SECONDS)
    parser.add_argument("--browsers", type=int, default=DEFAULT_BROWSERS)
    parser.add_argument("--arms", default="gpu-on,gpu-off", help="comma-separated: gpu-on, gpu-off")
    parser.add_argument("--out", type=Path, default=None)
    opts = parser.parse_args()

    arms = [a.strip() for a in opts.arms.split(",") if a.strip()]
    results: list[BlockResult] = []
    async with async_playwright() as p:
        for round_index in range(opts.rounds):
            # Alternate order so a burst cannot systematically favour one arm.
            order = arms if round_index % 2 == 0 else list(reversed(arms))
            for arm in order:
                result = await _run_block(p.chromium, arm, opts.block_seconds, opts.browsers)
                results.append(result)
                print(
                    f"round {round_index + 1} {arm:<8} "
                    f"{result.cycles:>3} cycles  {result.launched:>4} launched  "
                    f"{result.crashes:>4} crashes  {result.ips_new:>3} new .ips",
                    flush=True,
                )

    print("\n=== per arm ===")
    for arm in arms:
        blocks = [r for r in results if r.arm == arm]
        crashes = sum(r.crashes for r in blocks)
        launched = sum(r.launched for r in blocks)
        hot = sum(1 for r in blocks if r.crashes)
        print(
            f"  {arm:<8} {crashes:>5} crashes over {launched:>5} launches "
            f"in {len(blocks)} block(s); {hot} block(s) saw any"
        )
    if opts.out:
        opts.out.write_text(json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8")
        print(f"\nwrote {opts.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
