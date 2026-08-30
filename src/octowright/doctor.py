# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Diagnose the machine octowright is running on, before blaming octowright.

Written after a local test suite wedged for hours and the answer turned out to
be a WebKit build that could not navigate to ``about:blank`` -- provable in
about fifteen seconds with raw Playwright, but only once someone thought to
ask. Everything here exists to make that fifteen seconds the FIRST thing that
happens rather than the last.

The engine probe deliberately imports ``playwright`` and nothing else from this
project. That is the whole diagnostic value: if the probe fails, the engine is
broken and no amount of reading octowright's launch pipeline will help; if the
probe passes and octowright still cannot launch, the bug is ours. Routing the
probe through ``BrowserPool`` would collapse the two cases back together and
answer neither.

Every check returns the same ``Check`` shape so the CLI can render a table and
``--json`` can hand the identical data to a machine.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from provide.telemetry import get_logger

log = get_logger(__name__)

Status = Literal["ok", "warn", "fail", "skip"]

ENGINES: tuple[str, ...] = ("chromium", "firefox", "webkit")

# Per-step budget for the engine probe. Generous next to a healthy engine
# (chromium completes the WHOLE sequence in ~0.3s, firefox in ~1.6s, both
# measured) and short enough that probing three broken engines is a minute
# rather than an afternoon. A hung step is the finding, so the budget only has
# to be long enough that slowness is never mistaken for a hang.
DEFAULT_ENGINE_TIMEOUT_SECONDS = 30.0

# Substring identifying the Playwright driver process. The driver is the node
# process that owns the browsers; `process_reaper` tracks the BROWSERS, whose
# orphan rule is "my driver died". Nothing tracked the drivers themselves,
# which is how a test run accumulated nine of them unnoticed.
_DRIVER_PATH_SUBSTRING = "playwright/driver/node"


@dataclass(frozen=True)
class Check:
    """One diagnostic result.

    ``detail`` is the human line; ``data`` carries the same facts structurally
    so ``--json`` never has to be parsed back out of prose.
    """

    name: str
    status: Status
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def _engine_probe_source(kind: str) -> str:
    """The probe body, as a string, to run in a SEPARATE interpreter.

    Subprocess rather than in-process, for one measured reason: a wedged engine
    does not just fail, it leaves the Playwright driver and browser alive and
    the awaiting coroutine unkillable from inside its own loop -- cancelling
    releases the caller but cannot make the driver abandon a call already sent.
    Probing three engines in one process therefore risks the second probe
    inheriting the first one's wreckage, which is exactly the confusion this
    command exists to remove. A child process can simply be killed, and its
    driver and browsers die with it.
    """
    return f"""
import asyncio, json, sys, time
from playwright.async_api import async_playwright

STEPS = []

async def main():
    t0 = time.monotonic()
    def mark(name):
        STEPS.append({{"step": name, "at_s": round(time.monotonic() - t0, 3)}})
    pw = await async_playwright().start()
    mark("driver_start")
    browser = await pw.{kind}.launch(headless=True)
    mark("launch")
    ctx = await browser.new_context()
    mark("new_context")
    page = await ctx.new_page()
    mark("new_page")
    await page.goto("about:blank")
    mark("goto")
    await page.evaluate("1 + 1")
    mark("evaluate")
    await ctx.add_init_script("window.__octowright_doctor = 1")
    mark("add_init_script")
    await browser.close()
    await pw.stop()
    mark("close")

try:
    asyncio.run(main())
    print(json.dumps({{"ok": True, "steps": STEPS}}))
except BaseException as exc:
    print(json.dumps({{"ok": False, "steps": STEPS, "error": f"{{type(exc).__name__}}: {{exc}}"[:300]}}))
    sys.exit(1)
"""


def _next_step_after(steps: list[dict[str, Any]]) -> str:
    """Name the step that never completed, from the ones that did."""
    order = [
        "driver_start",
        "launch",
        "new_context",
        "new_page",
        "goto",
        "evaluate",
        "add_init_script",
        "close",
    ]
    done = {s["step"] for s in steps}
    for name in order:
        if name not in done:
            return name
    return "unknown"


async def probe_engine(kind: str, *, timeout: float = DEFAULT_ENGINE_TIMEOUT_SECONDS) -> Check:
    """Launch *kind* in a child interpreter and walk it through a real page.

    Reports the first step that did not complete, which is the difference
    between "WebKit is broken" and a stack trace nobody can act on.
    """
    import sys

    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _engine_probe_source(kind),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await proc.communicate()
    except TimeoutError:
        # The child owns the driver and the browser; killing it takes both
        # down, which is why the probe is a child in the first place.
        proc.kill()
        await proc.wait()
        elapsed = round(time.monotonic() - started, 2)
        return Check(
            name=f"engine:{kind}",
            status="fail",
            detail=f"HUNG — no result in {timeout:g}s (killed). This engine cannot drive a page on this machine.",
            data={"kind": kind, "hung": True, "elapsed_s": elapsed},
        )

    elapsed = round(time.monotonic() - started, 2)
    payload = _parse_probe_output(stdout)
    if payload is None:
        tail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return Check(
            name=f"engine:{kind}",
            status="fail",
            detail=f"probe produced no result in {elapsed}s: {tail[-1] if tail else '(no output)'}",
            data={"kind": kind, "elapsed_s": elapsed},
        )

    steps = payload.get("steps") or []
    if payload.get("ok"):
        return Check(
            name=f"engine:{kind}",
            status="ok",
            detail=f"launch → page → goto → evaluate in {elapsed}s",
            data={"kind": kind, "elapsed_s": elapsed, "steps": steps},
        )
    return Check(
        name=f"engine:{kind}",
        status="fail",
        detail=f"failed at step '{_next_step_after(steps)}' after {elapsed}s: {payload.get('error', '?')}",
        data={"kind": kind, "elapsed_s": elapsed, "steps": steps, "error": payload.get("error")},
    )


def _parse_probe_output(stdout: bytes | None) -> dict[str, Any] | None:
    """Last JSON line the child printed, or None if it printed none.

    Reads the LAST line rather than the whole stream because Playwright and the
    engines write their own noise to stdout on some platforms; the probe's own
    result is always the final line it prints.
    """
    import json

    if not stdout:
        return None
    for line in reversed(stdout.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _process_table() -> list[tuple[int, int, str]]:
    from octowright.process_reaper import list_processes

    return list_processes()


def stray_driver_pids() -> list[int]:
    """Playwright driver processes whose parent is gone.

    Same orphan rule ``process_reaper`` applies to browsers -- a live parent
    means somebody still owns it, so a running daemon's driver is never
    reported. Only a driver reparented away from a dead owner counts.
    """
    table = _process_table()
    live = {pid for pid, _ppid, _cmd in table}
    return [
        pid
        for pid, ppid, cmd in table
        if _DRIVER_PATH_SUBSTRING in cmd.replace("\\", "/") and (ppid <= 1 or ppid not in live)
    ]


def check_stray_drivers() -> Check:
    pids = stray_driver_pids()
    if not pids:
        return Check("processes:drivers", "ok", "no orphaned Playwright drivers", {"pids": []})
    return Check(
        "processes:drivers",
        "warn",
        f"{len(pids)} orphaned Playwright driver process(es); rerun with --fix to reap",
        {"pids": pids},
    )


def check_orphan_browsers() -> Check:
    from octowright.process_reaper import find_browser_pids

    pids = find_browser_pids("orphaned")
    if not pids:
        return Check("processes:browsers", "ok", "no orphaned browser processes", {"pids": []})
    return Check(
        "processes:browsers",
        "warn",
        f"{len(pids)} orphaned browser process(es); rerun with --fix to reap",
        {"pids": pids},
    )


def check_daemon() -> Check:
    """Whether a leader is recorded, and whether that record is still true."""
    from octowright.singleton import is_stale, pid_is_alive, read_lock

    info = read_lock()
    if info is None:
        return Check("daemon", "ok", "no daemon running (no lockfile)", {"running": False})
    alive = pid_is_alive(info.pid)
    if not alive or is_stale(info):
        return Check(
            "daemon",
            "warn",
            f"lockfile names pid {info.pid}, which is not a live daemon — stale lock",
            {"running": False, "pid": info.pid, "stale": True},
        )
    return Check(
        "daemon",
        "ok",
        f"leader pid {info.pid} at {info.http_host}:{info.http_port}",
        {"running": True, "pid": info.pid, "url": f"http://{info.http_host}:{info.http_port}"},
    )


def check_browser_installs() -> Check:
    """Are the engine builds Playwright expects actually on disk?

    Reported as a WARN rather than a FAIL: a deployment that only ever drives
    Chromium is not broken because it never installed WebKit, and calling that
    a failure would train people to ignore the command.
    """
    missing = [kind for kind in ENGINES if not _engine_executable_exists(kind)]
    if not missing:
        return Check("browsers:installed", "ok", "chromium, firefox, webkit all present", {"missing": []})
    return Check(
        "browsers:installed",
        "warn",
        f"not installed: {', '.join(missing)} — run `playwright install {' '.join(missing)}`",
        {"missing": missing},
    )


def _browsers_root() -> Path:
    """Where Playwright keeps its downloaded browser builds."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return Path(override)
    home = Path.home()
    if os.name == "nt":
        return home / "AppData" / "Local" / "ms-playwright"
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "ms-playwright"
    return home / ".cache" / "ms-playwright"


def _engine_executable_exists(kind: str) -> bool:
    """Is a build for *kind* on disk?

    Reads the download directory rather than asking Playwright for
    ``executable_path``. The obvious implementation -- ``sync_playwright()`` --
    cannot run here at all: these checks are awaited from ``run_checks``, and
    starting the sync API inside a running event loop raises, so every engine
    came back "not installed" while all three were demonstrably launching. A
    directory listing needs no driver and no loop.
    """
    root = _browsers_root()
    if not root.is_dir():
        return False
    return any(child.name.startswith(f"{kind}-") for child in root.iterdir())


def check_storage() -> Check:
    """Storage roots exist and are not world-readable where they hold secrets."""
    from octowright.defaults import MACROS_DIR, PROFILES_DIR, RECORDINGS_DIR

    private = {"recordings": RECORDINGS_DIR, "profiles": PROFILES_DIR}
    roots = {**private, "macros": MACROS_DIR}
    data: dict[str, Any] = {}
    loose: list[str] = []
    for label, path in roots.items():
        exists = path.exists()
        mode = oct(path.stat().st_mode & 0o777) if exists else None
        data[label] = {"path": str(path), "exists": exists, "mode": mode}
        if exists and label in private and (path.stat().st_mode & 0o077):
            loose.append(f"{label} ({mode})")
    if loose:
        return Check(
            "storage",
            "warn",
            f"readable by other local users: {', '.join(loose)} — expected 0700",
            data,
        )
    return Check("storage", "ok", "roots present with owner-only permissions", data)


async def run_checks(
    *,
    engines: bool = True,
    engine_timeout: float = DEFAULT_ENGINE_TIMEOUT_SECONDS,
) -> list[Check]:
    """Every check, engine probes last because they are the slow ones."""
    checks = [check_daemon(), check_browser_installs(), check_stray_drivers(), check_orphan_browsers(), check_storage()]
    if not engines:
        checks.append(Check("engines", "skip", "engine probes skipped (--skip-engines)", {}))
        return checks
    # Sequential, not gathered: three browsers launching at once on a sick
    # machine is how you turn a diagnosis into a second incident, and the whole
    # run is a few seconds when the engines are healthy.
    for kind in ENGINES:
        checks.append(await probe_engine(kind, timeout=engine_timeout))
    return checks


def worst_status(checks: list[Check]) -> Status:
    for status in ("fail", "warn", "ok"):
        if any(c.status == status for c in checks):
            return status
    return "skip"


def reap(*, dry_run: bool) -> dict[str, Any]:
    """Kill orphaned drivers and browsers. Never touches anything with a live parent."""
    from octowright.process_reaper import reap_orphan_browsers

    driver_pids = stray_driver_pids()
    if not dry_run:
        for pid in driver_pids:
            try:
                os.kill(pid, 15)
            except OSError as exc:
                log.debug("octowright.doctor.driver_kill_failed", pid=pid, error=repr(exc))
    browsers = reap_orphan_browsers("orphaned", dry_run=dry_run)
    return {"drivers": driver_pids, "browsers": browsers}
