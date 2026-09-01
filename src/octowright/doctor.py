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

from octowright.singleton import pid_is_alive, read_lock

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

# Budget for the CoreAudio probe. Measured on a healthy machine at 0.12-0.15s
# (three consecutive runs, including interpreter startup and framework load),
# so this is ~35x headroom. It only has to be long enough that a slow machine
# is never mistaken for a wedged daemon; a wedged one never answers at all.
_COREAUDIO_TIMEOUT_SECONDS = 5.0


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


def _coreaudio_probe_source() -> str:
    """Source for a child that asks CoreAudio for the size of the device list.

    Deliberately the SAME call WebKit's GPU process makes on startup rather
    than an approximation of it: ``AudioObjectGetPropertyData`` against the
    system object drives ``HALSystem::InitializeDevices``, which is the exact
    frame the GPU process was found blocked in. Probing anything else would
    answer a question nobody asked.

    ctypes rather than shelling out to ``system_profiler``: it is the same
    underlying call at a fraction of the cost (0.12s against 0.46s measured
    on a healthy machine), and it needs no parsing to decide the verdict.
    """
    return (
        "import ctypes, json, time\n"
        "t0 = time.monotonic()\n"
        "ca = ctypes.CDLL('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')\n"
        "class A(ctypes.Structure):\n"
        "    _fields_ = [('s', ctypes.c_uint32), ('c', ctypes.c_uint32), ('e', ctypes.c_uint32)]\n"
        "def f(v):\n"
        "    return (ord(v[0]) << 24) | (ord(v[1]) << 16) | (ord(v[2]) << 8) | ord(v[3])\n"
        "addr = A(f('dev#'), f('glob'), 0)\n"
        "size = ctypes.c_uint32(0)\n"
        "st = ca.AudioObjectGetPropertyDataSize(\n"
        "    ctypes.c_uint32(1), ctypes.byref(addr), ctypes.c_uint32(0), None, ctypes.byref(size))\n"
        "print(json.dumps({'ok': st == 0, 'status': int(st), 'elapsed_s': round(time.monotonic() - t0, 3)}))\n"
    )


async def check_coreaudio(*, timeout: float = _COREAUDIO_TIMEOUT_SECONDS) -> Check:
    """CoreAudio answers, so WebKit's GPU process can finish starting.

    This is a browser check wearing an audio check's name. WebKit's GPU process
    calls into CoreAudio on every startup
    (``GPUConnectionToWebProcess::enableMediaPlaybackIfNecessary``); when
    ``coreaudiod``'s HAL is wedged that call never returns, WebKit's own
    watchdog declares the GPU process unresponsive after ~3s and SIGKILLs it,
    relaunches it, and it hangs again -- so WebContent never gets a renderer
    and every navigation dies. Observed on 2026-08-30 as a WebKit that failed
    ``goto about:blank`` at ~6.7s with no crash report and a GPU pid that
    changed three times in one six-second run. ``killall coreaudiod`` fixed it
    outright: the same probe went from never completing to 0.97s end to end.

    It runs in a child process for a reason the engine probes share and then
    exceed: the wedged call blocks in ``mach_msg``, where a pending SIGTERM
    cannot be delivered, so ``timeout`` alone does not kill it (measured:
    plain ``timeout`` failed and ``timeout -s KILL`` returned 137). Only
    ``proc.kill()`` -- SIGKILL on POSIX -- reliably reaps it.

    Cheap enough to run even under ``--skip-engines``, which is the point: it
    names the CAUSE, where the WebKit engine probe reports only the symptom.
    """
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _coreaudio_probe_source(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await proc.communicate()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return Check(
            name="audio:coreaudio",
            status="fail",
            detail=(
                f"CoreAudio did not answer in {timeout:g}s — coreaudiod is wedged. "
                "WebKit's GPU process hangs on startup and is watchdog-killed, so WebKit "
                "cannot load any page. Fix: sudo killall coreaudiod (it respawns)."
            ),
            data={"hung": True, "elapsed_s": round(time.monotonic() - started, 2)},
        )

    elapsed = round(time.monotonic() - started, 2)
    payload = _parse_probe_output(stdout)
    if payload is None:
        tail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return Check(
            name="audio:coreaudio",
            status="warn",
            detail=f"probe produced no result in {elapsed}s: {tail[-1] if tail else '(no output)'}",
            data={"elapsed_s": elapsed},
        )
    if not payload.get("ok"):
        return Check(
            name="audio:coreaudio",
            status="warn",
            detail=f"CoreAudio answered with OSStatus {payload.get('status')} in {elapsed}s",
            data={"elapsed_s": elapsed, "status": payload.get("status")},
        )
    return Check(
        name="audio:coreaudio",
        status="ok",
        detail=f"CoreAudio answered in {elapsed}s",
        data={"elapsed_s": elapsed},
    )


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


async def check_canonical_port() -> Check:
    """Whether a SECOND, uncoordinated leader is also alive on the canonical port.

    ``check_daemon`` only reports on the lockfile's recorded leader. A daemon
    started outside octowright's own election path -- e.g. a systemd unit
    whose ``ExecStart`` runs ``serve --daemon-mode`` directly, which by design
    skips the lock/election check -- can bind the canonical port at the same
    moment a CLI-triggered spawn wins the race and lands on a bumped port
    instead. Both stay alive; the lockfile records only one of them, so
    ``check_daemon`` alone reports a clean single leader while a second one
    answers unrecorded.
    """
    from octowright import defaults
    from octowright.cli._leader_election import _canonical_port_serves_octowright

    info = read_lock()
    if info is None or not pid_is_alive(info.pid):
        return Check("daemon:canonical-port", "skip", "no live recorded leader -- nothing to cross-check", {})

    canonical_host, canonical_port = defaults.HTTP_HOST, defaults.HTTP_PORT
    data = {"canonical_port": canonical_port, "leader_port": info.http_port}
    if info.http_port == canonical_port:
        return Check(
            "daemon:canonical-port",
            "ok",
            f"leader is on the canonical port ({canonical_host}:{canonical_port})",
            data,
        )

    if await _canonical_port_serves_octowright(None, None):
        return Check(
            "daemon:canonical-port",
            "fail",
            f"two live daemons: the recorded leader on {info.http_host}:{info.http_port}, and a SECOND, "
            f"uncoordinated one answering on the canonical port {canonical_host}:{canonical_port} -- "
            "likely a systemd/launchd-managed daemon started outside octowright's election lock; "
            "run `octowright restart --keep-browsers` to reclaim the canonical port",
            data,
        )
    return Check(
        "daemon:canonical-port",
        "warn",
        f"leader is on {info.http_host}:{info.http_port}, not the canonical port "
        f"{canonical_host}:{canonical_port} -- it port-walked away from a conflict that may have "
        "since cleared",
        data,
    )


async def check_followers() -> Check:
    """Do the live followers run the same code as the daemon answering for them?

    A follower is a subprocess its MCP client owns, and it deliberately SURVIVES
    a leader restart so the client is not dropped. The consequence is that
    upgrading octowright and restarting the daemon updates the leader and
    nothing else: every connected client keeps running whatever follower it
    spawned, until that client reconnects. Observed here with followers two
    releases behind a current leader, driving browsers, with `doctor` reporting
    all-PASS -- because nothing in doctor looked at followers at all.

    Compared against the RUNNING DAEMON's version rather than this process's
    ``VERSION``: doctor is usually invoked from a checkout that has already been
    upgraded, so its own version is what the daemon *will* be after a restart,
    not what is answering now. Using it would report skew against a version
    nobody is running.

    A warn, never a fail: skew is a deployment state the operator resolves per
    client, not a broken machine, and `--fix` cannot touch it (killing a
    follower just breaks that client's session -- the client does not respawn a
    dead stdio server).
    """
    from octowright import defaults
    from octowright.bridge_state import read_state, stale_follower_count, summarize_state

    leader = await _running_leader_version()
    if leader is None:
        return Check("followers", "skip", "no daemon answering — nothing to compare followers against", {})

    summary = summarize_state(read_state(defaults.BRIDGE_STATE_PATH))
    versions: dict[str, int] = summary.get("follower_versions") or {}
    live = summary.get("follower_count", 0)
    dead = summary.get("dead_follower_count", 0)
    # summarize_state compares against THIS process's VERSION; recompute against
    # the daemon that is actually answering, through the same shared predicate
    # so only the baseline differs.
    stale = stale_follower_count(versions, leader)
    data = {
        "leader_version": leader,
        "follower_versions": versions,
        "live_followers": live,
        "stale_followers": stale,
        "dead_followers_ignored": dead,
    }
    if not live:
        return Check("followers", "ok", f"no live followers (leader {leader})", data)
    if stale:
        spread = ", ".join(f"{v} x{c}" for v, c in sorted(versions.items()) if v != leader)
        return Check(
            "followers",
            "warn",
            f"{stale} of {live} live follower(s) behind leader {leader} ({spread}) — "
            "each client must reconnect; a daemon restart cannot update them",
            data,
        )
    return Check("followers", "ok", f"all {live} live follower(s) on leader version {leader}", data)


async def _running_leader_version() -> str | None:
    """Version reported by the daemon currently answering, or None if none is."""
    import httpx2

    from octowright.singleton import pid_is_alive, read_lock

    info = read_lock()
    if info is None or not pid_is_alive(info.pid):
        return None
    try:
        async with httpx2.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"http://{info.http_host}:{info.http_port}/api/health")
        if response.status_code != 200:
            return None
        body = response.json()
    except (httpx2.HTTPError, OSError, ValueError):
        return None
    version = body.get("version") if isinstance(body, dict) else None
    return version if isinstance(version, str) and version else None


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
    """Storage roots exist and, on POSIX, are not readable by other local users.

    The permission half is POSIX-only, and skipping it on Windows is a fix
    rather than a gap. Windows has no POSIX mode bits: ``os.chmod`` there sets
    only the read-only flag, and a directory's ``st_mode`` comes back with the
    group and other bits set no matter who can actually reach it. Checking them
    anyway made every Windows machine report "readable by other local users"
    about roots that were fine -- a permanent false alarm, which is how a
    diagnostic command teaches people to ignore it. Access there is governed by
    ACLs, which this does not attempt to read.
    """
    from octowright.defaults import MACROS_DIR, PROFILES_DIR, RECORDINGS_DIR

    private = {"recordings": RECORDINGS_DIR, "profiles": PROFILES_DIR}
    roots = {**private, "macros": MACROS_DIR}
    posix = os.name != "nt"
    data: dict[str, Any] = {"permissions_checked": posix}
    loose: list[str] = []
    for label, path in roots.items():
        exists = path.exists()
        mode = oct(path.stat().st_mode & 0o777) if exists and posix else None
        data[label] = {"path": str(path), "exists": exists, "mode": mode}
        if posix and exists and label in private and (path.stat().st_mode & 0o077):
            loose.append(f"{label} ({mode})")
    if loose:
        return Check(
            "storage",
            "warn",
            f"readable by other local users: {', '.join(loose)} — expected 0700",
            data,
        )
    if not posix:
        return Check("storage", "ok", "roots present (permissions are ACL-governed on Windows, not checked)", data)
    return Check("storage", "ok", "roots present with owner-only permissions", data)


async def run_checks(
    *,
    engines: bool = True,
    engine_timeout: float = DEFAULT_ENGINE_TIMEOUT_SECONDS,
) -> list[Check]:
    """Every check, engine probes last because they are the slow ones."""
    checks = [check_daemon(), check_browser_installs(), check_stray_drivers(), check_orphan_browsers(), check_storage()]
    # macOS only, and gated here rather than returning a "skip" from the check
    # itself: CoreAudio is the wedge that silently breaks WebKit on a Mac, and
    # a permanent SKIP line on every Linux run is noise a reader learns to
    # scroll past. Ordered before the engine probes so the CAUSE is on screen
    # above the symptom, and outside the --skip-engines return because it is a
    # sub-second check that stays useful when the probes are turned off.
    if sys.platform == "darwin":
        checks.append(await check_coreaudio())
    # Cheap (one loopback probe) and, like coreaudio, most useful exactly when
    # the engine probes are skipped: it answers "is this deployment consistent",
    # which no other check covers.
    checks.append(await check_canonical_port())
    checks.append(await check_followers())
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
