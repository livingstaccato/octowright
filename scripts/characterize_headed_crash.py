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
Chrome for Testing 151.0.7922.34 the ``raw`` arm produced **912 headed launches
across interleaved gpu-on/gpu-off blocks with zero crashes**, and minutes later
a browser died during an ordinary ``make test`` with the byte-exact
characterized signature: ``EXC_BREAKPOINT``/``SIGTRAP`` on ``CrBrowserMain``,
reached through ``__CFRUNLOOP_IS_CALLING_OUT_TO_A_SOURCE0_PERFORM_FUNCTION__``,
2.2 seconds after launch. So the bug is present on 151 and that arm did not
reproduce it. (The ``pool`` arm added below since has -- see the 2026-09-07
note -- but a clean block still proves nothing either way, because the crash
arrives in bursts.)

The likely reason the raw arm stays clean is the isolation this script was
originally built with. It
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
* ``pool-sigterm`` -- as ``pool``, but each cycle ends by **SIGTERM-ing the
  Playwright driver** instead of shutting the pool down gracefully. See below.

That separation is the same one ``doctor``'s engine probes draw, and it is
worth keeping for the same reason: collapsing the two answers neither.
``--browsers`` still governs concurrency, and the pool arm deliberately sets
``OCTOWRIGHT_HEADED_LAUNCH_CONCURRENCY`` to that number so the semaphore
shipped as a mitigation cannot silently throttle the storm being measured.

**Why ``pool-sigterm`` exists.** The only real correlation obtained so far, on
2026-09-06, put a genuine ``CrBrowserMain`` abort at the end of six consecutive
seconds of ``tests/test_protect_headed_launch_live.py`` -- one headed launch per
second. What the suite does between those launches and this harness did not is
the distinguishing factor: ``tests/conftest.py`` SIGTERMs the driver of any pool
still holding one at EVERY test teardown, so the suite's shape is launch ->
headed browser -> driver killed -> launch again, once a second. The ``pool`` arm
calls ``pool.shutdown()``, a graceful ``pw.stop()``. A SIGTERM probe on its own
(one idle browser, one killed driver) also produced nothing, so if the lead is
right the trigger is the COMBINATION plus repetition, not either alone. Pair it
with ``--browsers 1`` to reproduce the sequential shape the correlation shows.

It is a lead, not a conclusion: one correlation, and the +/-20s window around it
holds many tests. **It also turned out to be wrong, and the run that disproved
it is the first reproduction this harness has ever managed** -- see below.

**2026-09-07: REPRODUCED, on the ``pool`` arm, and the abort is at CLOSE.**
Four interleaved rounds of ``pool``/``pool-sigterm`` at ``--browsers 1``,
Playwright 1.62.0 / Chromium 151.0.7922.34 / macOS 26::

    r1 pool           134 cycles    26 new .ips  (all CrBrowserMain)
    r1 pool-sigterm   178 cycles     0
    r2 pool-sigterm   176 cycles     0
    r2 pool           133 cycles     0
    r3 pool           171 cycles     0
    r3 pool-sigterm   193 cycles     0
    r4 pool-sigterm   185 cycles     0
    r4 pool           165 cycles     0

    pool          26 reports / 603 launches / 4 blocks -- 1 block saw any
    pool-sigterm   0 reports / 732 launches / 4 blocks

The 26 are byte-exact to the characterised field crash -- ``EXC_BREAKPOINT`` /
``SIGTRAP`` on ``CrBrowserMain``, through
``__CFRUNLOOP_IS_CALLING_OUT_TO_A_SOURCE0_PERFORM_FUNCTION__`` -> AppKit
``nextEventMatchingMask:`` -> ``ChromeMain``.

Two things follow, and the second is the useful one:

* **Read 26-against-0 as a burst, not a rate.** The other three ``pool``
  blocks, same arm and same config, scored 0 over 469 cycles. That is precisely
  the burstiness the Method note above describes, and precisely why the arms
  are interleaved. A single block cannot attribute the crash to an arm -- and
  the burst landed in the run's **first** block, so "the first headed browsers
  after the machine has been idle of them" explains it as well as the arm does.
  Alternating the block order per round does not control for that, because the
  first block of the RUN is always the same arm; start the next run with
  ``--launchers pool-sigterm,pool`` or discard a warm-up block.
* **The abort lands at close.** Each report carries ``procLaunch`` and
  ``captureTime``: across all 26 the browser lived **1.09-1.76s, mean 1.31s**,
  against a measured cycle time of 1.34s. Every one died at the end of its
  life, while the cycle was closing it. The field crash recorded as "2.2s after
  launch" fits that shape too.

So ``pool-sigterm``'s zero has a *mechanism* and not merely a caveat: killing
the driver takes the browser down by signal before it can run its own shutdown
path, leaving no graceful teardown in which to abort. Do not report that arm's
zero as "SIGTERM prevents the crash" -- it is a weaker detector by construction.
Working hypothesis: headed Chromium 151 aborts during its own graceful
shutdown on the native AppKit path, conditional on something the clean ``pool``
blocks did not have.

**That decisive run was then attempted, and found nothing.** Six interleaved
rounds of ``raw``/``pool`` an hour later, ``raw`` deliberately first so the
run's first-block position went to the arm hypothesised clean::

    raw    0 reports / 1262 launches / 6 blocks
    pool   0 reports /  872 launches / 6 blocks

Zero new reports on the machine at all. **That is not "no difference between
the arms" -- the crash did not occur, so there was nothing to compare**, and
the question is still open. The exposure comparison is the part worth keeping:
the same ``pool`` arm scored 26 in 134 launches earlier the same afternoon and
0 in 872 launches an hour later, so the burst is conditional on machine state
rather than on the arm.

**So do not reach for another blind A/B first.** Each 36-minute run is a coin
flip on whether the machine is even in the state, and the rate note above
already puts it at roughly one report per 24h of real work. Every field
observation came from the suite running, and the only correlation ever obtained
came from ``scripts/watch_test_timeline.py --correlate --newest-crash
--crash-thread CrBrowserMain``. Use that to find the state; use this harness to
A/B a hypothesis once it is known.

The pool-only difference to suspect first, when there is something to test
against, is ``browser_pool/options.py``'s tile placement -- it passes window
position/size argv the raw arm never sends, and the crash sits in the native
AppKit event path.

**Reading this arm's ``.ips`` column needs the faulting thread, not a count.**
Killing a driver makes its children abort, and those aborts write crash reports
of their own -- measured at 27 ``Chrome_ChildIOThread`` aborts against ONE real
``CrBrowserMain`` on a machine that had run the suite. So in this arm a raw
report count is dominated by damage the arm inflicts on purpose, and the numbers
are broken out per faulting thread instead. ``CrBrowserMain`` is the signature
being hunted; everything else in that column is this harness's own teardown.

Rate matters too: one crash report in 24 hours on a machine doing real work.
Any A/B must compare episode counts over matched wall-clock and run for hours.

Usage::

    # is it Chromium, or is it us?
    uv run --active python scripts/characterize_headed_crash.py --launchers raw,pool
    # the 2026-09-06 lead: sequential headed launch with the driver killed between
    uv run --active python scripts/characterize_headed_crash.py \
        --launchers pool,pool-sigterm --arms gpu-on --browsers 1 --rounds 4
    # does the GPU knob help, once something reproduces?
    uv run --active python scripts/characterize_headed_crash.py --arms gpu-on,gpu-off --rounds 6
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from octowright.browser_pool.crash_reports import DEFAULT_REPORTS_DIR
from octowright.session.timeouts import bounded

# Matches the shipped knob's flag set exactly; testing a different one would
# answer a question nobody can act on.
GPU_DISABLE_ARGS = ("--disable-gpu", "--disable-gpu-compositing")

# 8 reproduced it and 3 did not, so the storm arm uses 8.
DEFAULT_BROWSERS = 8
DEFAULT_BLOCK_SECONDS = 180.0
DEFAULT_ROUNDS = 1

LAUNCHERS = ("raw", "pool", "pool-sigterm")

# The faulting thread of the abort being hunted. Everything else this harness's
# .ips column reports in the sigterm arm is its own teardown killing children.
REAL_CRASH_THREAD = "CrBrowserMain"

# Imported, never spelled again here: crash_reports already owns this location
# for the daemon's own crash correlation, and two copies of an OS path drift.
_IPS_DIR = DEFAULT_REPORTS_DIR


@dataclass
class BlockResult:
    launcher: str
    arm: str
    seconds: float
    cycles: int
    launched: int
    crashes: int
    ips_new: int | None
    #: New reports keyed by faulting thread. `None` off macOS, where there is no
    #: report directory to read -- distinct from `{}`, which means "measured,
    #: and nothing crashed".
    ips_threads: dict[str, int] | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def real_crash_reports(self) -> int:
        """New reports on the hunted thread, ignoring self-inflicted ones."""
        return (self.ips_threads or {}).get(REAL_CRASH_THREAD, 0)


def _ips_supported() -> bool:
    """Whether the .ips corroboration column can be measured at all.

    macOS-only. It matters that this is explicit: a glob on a directory that
    does not exist returns an empty list rather than raising, so off macOS the
    column would silently read 0 -- indistinguishable from "measured, and there
    were none". The primary crash signal (the browser's own ``disconnected``
    event) is cross-platform and unaffected.
    """
    return sys.platform == "darwin"


def _ips_names() -> set[str]:
    """Chrome-ish crash reports currently on disk, by name.

    By NAME rather than count: macOS prunes this directory, so a count can fall
    while new reports arrive.
    """
    if not _ips_supported():
        return set()
    try:
        return {p.name for p in _IPS_DIR.glob("*.ips") if "hrome" in p.name}
    except OSError:
        return set()


def _ips_threads(names: set[str]) -> dict[str, int]:
    """Count *names* by the faulting thread each report blames.

    The parser is imported from ``watch_test_timeline`` rather than restated:
    two copies of an ``.ips`` reader drift, and that one is already the tool
    used to pick a crash by class. Imported lazily, mirroring how ``_pool_cycle``
    defers ``BrowserPool`` -- the raw arm needs neither.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from watch_test_timeline import crash_signature

    counts: dict[str, int] = {}
    for name in names:
        signature = crash_signature(_IPS_DIR / name)
        if signature is None:
            continue
        thread = signature[3] or "unknown"
        counts[thread] = counts.get(thread, 0) + 1
    return counts


def _driver_pid(pw: object) -> int | None:
    """OS pid of a live Playwright driver, or None if the chain has moved.

    Playwright exposes no public handle on the node process it spawned, so this
    walks ``_impl_obj._connection._transport._proc`` defensively: every hop is a
    ``getattr`` and a broken chain simply means no kill, never an error.

    ``tests/conftest.py`` carries the same walk for its driver reaper. They are
    deliberately separate copies: this script must never import a conftest, and
    a shared home in ``src/`` would ship introspection into Playwright privates
    that nothing in the package needs. A Playwright change breaks both loudly --
    the walk returns None and each caller simply stops killing.
    """
    node: object | None = pw
    for attr in ("_impl_obj", "_connection", "_transport", "_proc"):
        node = getattr(node, attr, None)
        if node is None:
            return None
    pid = getattr(node, "pid", None)
    return pid if isinstance(pid, int) else None


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
            # Bounded: an unresponsive browser leaves this awaiting a CDP
            # reply that never comes -- observed as a 15+ hour wedge with
            # near-zero CPU on both the driver and the orphaned browser, the
            # same class of hang the octowright.session.timeouts module
            # exists to bound everywhere else in the codebase.
            await bounded(browser.close(), operation="raw_browser_close")
        except Exception as exc:
            errors.append(f"close {type(exc).__name__}: {exc}"[:160])
    return errors


def _note_disconnect(browser: Any, state: dict[str, int]) -> None:
    if not getattr(browser, "_expected_close", False):
        state["crashes"] += 1


def _kill_driver(pool: Any) -> str | None:
    """SIGTERM the pool's Playwright driver, the way `tests/conftest.py` does.

    Returns an error string, or None on success (including "there was no driver
    to kill", which is not a failure -- a pool that never launched never started
    one). ``_pw`` is cleared first for the same reason the conftest clears it:
    leaving it set would have the caller retry a dead pid.
    """
    pw = getattr(pool, "_pw", None)
    if pw is None:
        return None
    pool._pw = None
    pid = _driver_pid(pw)
    if pid is None:
        return "driver pid unreachable (Playwright internals moved)"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return None  # Already gone; the arm's intent is satisfied either way.
    except OSError as exc:
        return f"kill {type(exc).__name__}: {exc}"[:160]
    return None


async def _pool_cycle(args: list[str], count: int, state: dict[str, int], *, sigterm: bool = False) -> list[str]:
    """Launch *count* headed browsers through a real BrowserPool, then close them.

    A fresh pool per cycle, matching the churn the raw arm produces: a pool
    reuses one Playwright driver, so keeping one alive across cycles would
    measure a different thing from the launch/relaunch storm being reproduced.

    With *sigterm*, the driver is killed instead of shut down gracefully and the
    per-session closes are skipped -- killing the driver takes the browsers with
    it, so closing them first would remove the abruptness that is the point of
    the arm. See the module docstring for why that difference is under test.
    """
    from octowright.browser_pool.pool import BrowserPool

    errors: list[str] = []
    # Removed in the finally below. A cycle is ~1.3s and a long run is hours, so
    # a leaked dir per cycle adds up fast: an early 25-minute run left 1,449 of
    # them behind. They are small (13MB in total there), which is exactly why
    # nothing would ever notice and why it has to be cleaned up here.
    recordings = Path(tempfile.mkdtemp(prefix="char-rec-"))
    pool = BrowserPool(recordings_dir=recordings)
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
        for session in [] if sigterm else list(pool.iter_sessions()):
            try:
                await pool.close(session.instance_id, force=True)
            except Exception as exc:
                errors.append(f"close {type(exc).__name__}: {exc}"[:160])
    finally:
        if sigterm:
            failure = _kill_driver(pool)
            if failure:
                errors.append(failure)
        else:
            with contextlib.suppress(Exception):
                await pool.shutdown()
        # After the driver is gone either way, so nothing is still writing.
        shutil.rmtree(recordings, ignore_errors=True)
    return errors


def _thread_note(result: BlockResult) -> str:
    """The per-thread breakdown of a block's new reports, for the live line.

    Empty when nothing crashed, so the ordinary case stays quiet. The hunted
    thread is spelled out rather than summarised because in the sigterm arm the
    bare count is mostly this harness's own teardown.
    """
    if not result.ips_threads:
        return ""
    parts = [f"{thread}={n}" for thread, n in sorted(result.ips_threads.items())]
    return "(" + " ".join(parts) + ")"


async def _run_block(browser_type: Any, arm: str, seconds: float, count: int, launcher: str) -> BlockResult:
    args = list(GPU_DISABLE_ARGS) if arm == "gpu-off" else []
    before = _ips_names()
    state = {"crashes": 0, "launched": 0}
    errors: list[str] = []
    cycles = 0
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if launcher.startswith("pool"):
            errors.extend(await _pool_cycle(args, count, state, sigterm=launcher == "pool-sigterm"))
        else:
            errors.extend(await _one_cycle(browser_type, args, count, state))
        cycles += 1
    # Reports are written a beat after the process dies.
    await asyncio.sleep(3)
    fresh = _ips_names() - before if _ips_supported() else set()
    return BlockResult(
        launcher=launcher,
        arm=arm,
        seconds=round(time.monotonic() - started, 1),
        cycles=cycles,
        launched=state["launched"],
        crashes=state["crashes"],
        ips_new=len(fresh) if _ips_supported() else None,
        ips_threads=_ips_threads(fresh) if _ips_supported() else None,
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
        help="comma-separated: raw (bare Playwright), pool (a real BrowserPool), "
        "pool-sigterm (a real BrowserPool whose driver is killed between cycles, "
        "the way the test suite's teardown does). Run raw with pool to separate "
        "'Chromium is broken' from 'we break it'; run pool with pool-sigterm to "
        "test whether the abrupt teardown is what the suite adds.",
    )
    parser.add_argument("--out", type=Path, default=None)
    opts = parser.parse_args()

    arms = [a.strip() for a in opts.arms.split(",") if a.strip()]
    launchers = [x.strip() for x in opts.launchers.split(",") if x.strip()]
    unknown = set(launchers) - set(LAUNCHERS)
    if unknown:
        parser.error(f"unknown launcher(s): {', '.join(sorted(unknown))}")

    # The shipped semaphore would otherwise throttle the very storm being
    # measured, and a mitigation silently changing the experiment is how a
    # negative result gets believed.
    if any(x.startswith("pool") for x in launchers):
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
                    f"round {round_index + 1} {launcher:<12} {arm:<8} "
                    f"{result.cycles:>3} cycles  {result.launched:>4} launched  "
                    f"{result.crashes:>4} crashes  "
                    f"{'n/a' if result.ips_new is None else result.ips_new:>3} new .ips  "
                    f"{_thread_note(result)}",
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
            real = sum(r.real_crash_reports for r in blocks)
            hot = sum(1 for r in blocks if r.crashes or r.real_crash_reports)
            print(
                f"  {launcher:<12} {arm:<8} {crashes:>5} crashes over {launched:>5} launches "
                f"in {len(blocks)} block(s); {hot} block(s) saw any; "
                f"{real} {REAL_CRASH_THREAD} report(s)"
            )
    if opts.out:
        opts.out.write_text(json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8")
        print(f"\nwrote {opts.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
