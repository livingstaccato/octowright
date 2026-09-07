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

**Measured limitation -- a clean run here is NOT evidence of absence.** On
Chrome for Testing 151.0.7922.34 this harness produced **912 headed launches
across interleaved gpu-on/gpu-off blocks with zero crashes**, and minutes later
a browser died during an ordinary ``make test`` with the byte-exact
characterized signature: ``EXC_BREAKPOINT``/``SIGTRAP`` on ``CrBrowserMain``,
reached through ``__CFRUNLOOP_IS_CALLING_OUT_TO_A_SOURCE0_PERFORM_FUNCTION__``,
2.2 seconds after launch. So the bug is present on 151 and this harness does
not reproduce it.

The likely reason is the isolation this script was originally built with. It
drove **raw Playwright** -- ``chromium.launch()``, an ephemeral context,
``about:blank``, no octowright code at all -- chosen to mirror ``doctor``'s
engine probes. That removes precisely the variables the crashing workload has:
persistent profile contexts, the init scripts injected into every page (badge,
title, macro pill), the viewport binding, and real navigation. The suite that
crashed goes through ``BrowserPool``.

So there are now two **launcher** arms, and picking between them is the point
of the script rather than a detail of it:

* ``raw`` -- ``chromium.launch()``, nothing of ours in the picture. If this
  crashes, the bug is Chromium's and no octowright change can fix it.
* ``pool`` -- a real ``BrowserPool.launch()`` per browser, so the init scripts,
  the viewport binding, the recorder and a real navigation are all present.
  If ``pool`` crashes where ``raw`` does not, the trigger is something we do,
  and the difference between the arms is the search space.

That separation is the same one ``doctor``'s engine probes draw, and it is
worth keeping for the same reason: collapsing the two answers neither.
``--browsers`` still governs concurrency, and the pool arm deliberately sets
``OCTOWRIGHT_HEADED_LAUNCH_CONCURRENCY`` to that number so the semaphore
shipped as a mitigation cannot silently throttle the storm being measured.

Rate matters too: one crash report in 24 hours on a machine doing real work.
Any A/B must compare episode counts over matched wall-clock and run for hours.

Usage::

    # is it Chromium, or is it us?
    uv run --active python scripts/characterize_headed_crash.py --launchers raw,pool
    # does the GPU knob help, once something reproduces?
    uv run --active python scripts/characterize_headed_crash.py --arms gpu-on,gpu-off --rounds 6
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import tempfile
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
    launcher: str
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


async def _pool_cycle(args: list[str], count: int, state: dict[str, int]) -> list[str]:
    """Launch *count* headed browsers through a real BrowserPool, then close them.

    A fresh pool per cycle, matching the churn the raw arm produces: a pool
    reuses one Playwright driver, so keeping one alive across cycles would
    measure a different thing from the launch/relaunch storm being reproduced.
    """
    from octowright.browser_pool.pool import BrowserPool

    errors: list[str] = []
    pool = BrowserPool(recordings_dir=Path(tempfile.mkdtemp(prefix="char-rec-")))
    try:
        results = await asyncio.gather(
            *(
                pool.launch(kind="chromium", headed=True, url="about:blank", disable_gpu=bool(args))
                for _ in range(count)
            ),
            return_exceptions=True,
        )
        for item in results:
            if isinstance(item, BaseException):
                errors.append(f"{type(item).__name__}: {item}"[:160])
                state["crashes"] += 1
            else:
                state["launched"] += 1
        for session in list(pool.iter_sessions()):
            try:
                await pool.close(session.instance_id, force=True)
            except Exception as exc:
                errors.append(f"close {type(exc).__name__}: {exc}"[:160])
    finally:
        with contextlib.suppress(Exception):
            await pool.shutdown()
    return errors


async def _run_block(browser_type: Any, arm: str, seconds: float, count: int, launcher: str) -> BlockResult:
    args = list(GPU_DISABLE_ARGS) if arm == "gpu-off" else []
    before = _ips_names()
    state = {"crashes": 0, "launched": 0}
    errors: list[str] = []
    cycles = 0
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if launcher == "pool":
            errors.extend(await _pool_cycle(args, count, state))
        else:
            errors.extend(await _one_cycle(browser_type, args, count, state))
        cycles += 1
    # Reports are written a beat after the process dies.
    await asyncio.sleep(3)
    return BlockResult(
        launcher=launcher,
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
    parser.add_argument(
        "--launchers",
        default="raw",
        help="comma-separated: raw (bare Playwright), pool (a real BrowserPool). "
        "Run both to separate 'Chromium is broken' from 'we break it'.",
    )
    parser.add_argument("--out", type=Path, default=None)
    opts = parser.parse_args()

    arms = [a.strip() for a in opts.arms.split(",") if a.strip()]
    launchers = [x.strip() for x in opts.launchers.split(",") if x.strip()]
    unknown = set(launchers) - {"raw", "pool"}
    if unknown:
        parser.error(f"unknown launcher(s): {', '.join(sorted(unknown))}")

    # The shipped semaphore would otherwise throttle the very storm being
    # measured, and a mitigation silently changing the experiment is how a
    # negative result gets believed.
    if "pool" in launchers:
        os.environ["OCTOWRIGHT_HEADED_LAUNCH_CONCURRENCY"] = str(opts.browsers)

    results: list[BlockResult] = []
    async with async_playwright() as p:
        for round_index in range(opts.rounds):
            # Alternate order so a burst cannot systematically favour one cell.
            cells = [(launcher, arm) for launcher in launchers for arm in arms]
            if round_index % 2:
                cells.reverse()
            for launcher, arm in cells:
                result = await _run_block(p.chromium, arm, opts.block_seconds, opts.browsers, launcher)
                results.append(result)
                print(
                    f"round {round_index + 1} {launcher:<5} {arm:<8} "
                    f"{result.cycles:>3} cycles  {result.launched:>4} launched  "
                    f"{result.crashes:>4} crashes  {result.ips_new:>3} new .ips",
                    flush=True,
                )

    print("\n=== per cell ===")
    for launcher in launchers:
        for arm in arms:
            blocks = [r for r in results if r.arm == arm and r.launcher == launcher]
            if not blocks:
                continue
            crashes = sum(r.crashes for r in blocks)
            launched = sum(r.launched for r in blocks)
            hot = sum(1 for r in blocks if r.crashes)
            print(
                f"  {launcher:<5} {arm:<8} {crashes:>5} crashes over {launched:>5} launches "
                f"in {len(blocks)} block(s); {hot} block(s) saw any"
            )
    if opts.out:
        opts.out.write_text(json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8")
        print(f"\nwrote {opts.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
