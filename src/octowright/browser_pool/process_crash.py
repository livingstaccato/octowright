# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tell a browser-PROCESS crash apart from a window the user closed.

**What Playwright gives.** Nothing that separates the two. For a persistent
context, a user closing the last window and the browser process dying on a
signal both arrive as: every page fires ``close``, the context fires ``close``,
the browser fires ``disconnected``, and a pending call rejects with
``TargetClosedError('... Target page, context or browser has been closed')``.
No ``page.on("crash")`` fires for a dead browser process -- that event is a
renderer. The driver does learn the exit code and signal, but forwards them only
to ``launchServer``'s ``browserServer``, never to a client. So a SIGSEGV of the
browser reached octowright as ``reason="user_close"`` and was recorded as
``close ... reason: external`` (the Chromium download crash in
``download_history`` did exactly that, six times).

**What the OS gives instead** (measured live, headed, persistent context, N=8 per
engine and path; the user close is a real ``WM_DELETE_WINDOW``, the message a
window manager sends when the X is clicked):

========  =====================================  ================================
engine    user closes the last window            SIGTRAP / SIGSEGV / SIGKILL
========  =====================================  ================================
chromium  alive at last ``page.close``; exits    already dead (zombie) when the
          55-142 ms later; ``SingletonLock``      first ``page.close`` arrives,
          removed before ``context.close``        19-86 ms earlier; lock left behind
firefox   alive; exits 489-675 ms later           already dead, 26-70 ms earlier
webkit    alive; wrapper exits 21-29 ms later     already dead, 13-23 ms earlier
========  =====================================  ================================

A browser closing its own windows is still running while it tells us so; a
crashed one is gone before Playwright notices the pipe dropped. So the rule is:
**the browser process was already gone when its first evicting close signal
reached octowright** (:func:`exit_verdict`).

Liveness has one weakness: a stalled event loop delivers the orderly close late,
after the browser has exited, and would read as a crash. For a headed Chromium
the ``SingletonLock`` removes that weakness entirely -- once the process is dead
the lock's presence is final (orderly exit removes it, a signal death cannot),
so it decides both ways regardless of timing. The headless shell writes no lock,
and Firefox's ``lock`` measured inverted (kept on a clean close, removed by its
own SIGSEGV handler), so those fall back to liveness, whose margin is ~500 ms on
Firefox and ~20 ms on WebKit. Misreading is biased the safe way: an unknown pid,
an unreadable ``/proc``, or a reused pid all read as a close, which is what
octowright reported before this existed.

**Scope.** Linux (``/proc``), persistent contexts (a ``profile`` or ``session``
user-data-dir, which is what identifies the process). An ephemeral context and a
non-Linux host resolve no process and keep the old behaviour.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from provide.telemetry import get_logger

from octowright._tracing import counter
from octowright.browser_pool import incidents
from octowright.browser_pool.events import SessionCloseReason

log = get_logger(__name__)

PROC_ROOT: Path = Path("/proc")

ExitVerdict = Literal["crashed", "closed"]
ProcessState = Literal["alive", "dead", "unknown"]

_PROCESS_CRASHED = counter(
    "octowright_browser_process_crashed_total",
    description="Browser processes that died while their session was live (not a window close)",
)

# Zombie / dead in /proc/<pid>/stat. A zombie has exited and is only waiting
# for the driver to reap it -- the measured state of a crashed browser.
_DEAD_STATES: Final = frozenset({"Z", "X", "x"})

_SINGLETON_LOCK: Final = "SingletonLock"


@dataclass(frozen=True, slots=True)
class BrowserProcess:
    """The OS process behind one session, resolved once at launch."""

    pid: int
    user_data_dir: Path
    # Chromium wrote a SingletonLock for THIS run. Headed builds do and the
    # headless shell does not, so the lock is evidence only when it was there.
    singleton_lock: bool


def _argv(entry: Path) -> list[str] | None:
    try:
        raw = (entry / "cmdline").read_bytes()
    except OSError:
        return None
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def _stat_fields(pid_dir: Path) -> list[str] | None:
    """Fields after ``comm``. ``comm`` may hold spaces and parens, hence rsplit."""
    try:
        text = (pid_dir / "stat").read_text()
    except OSError:
        return None
    _, sep, rest = text.rpartition(")")
    return rest.split() if sep else None


def _names_profile(argv: list[str], spellings: frozenset[str]) -> bool:
    if len(argv) == 1 and " " in argv[0]:
        # Chromium rewrites its cmdline into one space-joined string, so the
        # flag has to be found inside it, bounded by a space or the end.
        padded = f" {argv[0]} "
        return any(f" --user-data-dir={spelling} " in padded for spelling in spellings)
    for index, token in enumerate(argv):
        if token.startswith("--user-data-dir=") and token.split("=", 1)[1] in spellings:
            return True
        if token in spellings and index > 0 and argv[index - 1] in ("-profile", "--profile"):
            return True
    return False


def _owned_by(entry: Path, uid: int | None) -> bool:
    if uid is None:
        return True
    try:
        return entry.stat().st_uid == uid
    except OSError:
        return False


def _ppid_if_named(entry: Path, spellings: frozenset[str]) -> int | None:
    """``entry``'s parent pid when its argv names the profile, else ``None``."""
    argv = _argv(entry)
    fields = _stat_fields(entry)
    if not argv or not fields or len(fields) < 2 or not _names_profile(argv, spellings):
        return None
    return int(fields[1])


def _candidates(user_data_dir: Path, proc_root: Path) -> dict[int, int]:
    """``{pid: ppid}`` of this user's processes whose argv names the profile."""
    spellings = frozenset({str(user_data_dir), str(user_data_dir.expanduser().resolve())})
    uid = os.getuid() if hasattr(os, "getuid") else None
    found: dict[int, int] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or not _owned_by(entry, uid):
            continue
        ppid = _ppid_if_named(entry, spellings)
        if ppid is not None:
            found[int(entry.name)] = ppid
    return found


def find_browser_process(
    kind: str, user_data_dir: Path | str, *, proc_root: Path | None = None
) -> BrowserProcess | None:
    """Resolve the browser process of a persistent context from ``/proc``.

    The root-most process whose argv names the user-data-dir: Chromium's
    browser process (its zygote repeats the flag, renderers do not), Firefox's
    parent (content processes carry no ``-profile``), and WebKit's
    ``pw_run.sh`` wrapper, which holds the pipe too and exits when MiniBrowser
    does. Two unrelated roots is ambiguous and resolves to ``None`` rather than
    a guess. Best-effort: any failure is ``None``.
    """
    root = proc_root if proc_root is not None else PROC_ROOT
    udd = Path(user_data_dir)
    try:
        if not root.is_dir():
            return None
        found = _candidates(udd, root)
    except OSError as exc:
        log.debug("octowright.browser.process_scan_failed", error=repr(exc))
        return None
    roots = [pid for pid, ppid in found.items() if ppid not in found]
    if len(roots) != 1:
        return None
    lock = kind == "chromium" and os.path.lexists(udd / _SINGLETON_LOCK)
    return BrowserProcess(pid=roots[0], user_data_dir=udd, singleton_lock=lock)


def process_state(pid: int, *, proc_root: Path | None = None) -> ProcessState:
    """``alive``, ``dead`` (zombie or reaped), or ``unknown`` (no ``/proc``)."""
    root = proc_root if proc_root is not None else PROC_ROOT
    if not root.is_dir():
        return "unknown"
    pid_dir = root / str(pid)
    if not pid_dir.exists():
        return "dead"
    fields = _stat_fields(pid_dir)
    if not fields:
        # Reaped between the exists() and the read, or unreadable.
        return "dead" if not pid_dir.exists() else "unknown"
    return "dead" if fields[0] in _DEAD_STATES else "alive"


def exit_verdict(proc: BrowserProcess | None, *, proc_root: Path | None = None) -> ExitVerdict | None:
    """Classify the exit at the evicting close signal; ``None`` = cannot tell."""
    if proc is None:
        return None
    state = process_state(proc.pid, proc_root=proc_root)
    if state == "unknown":
        return None
    if state == "alive":
        return "closed"
    if proc.singleton_lock:
        return "crashed" if os.path.lexists(proc.user_data_dir / _SINGLETON_LOCK) else "closed"
    return "crashed"


def _evidence(proc: BrowserProcess) -> str:
    return "singleton_lock_left_behind" if proc.singleton_lock else "process_gone_before_close"


def _relaunch_scheduled(session: Any) -> bool:
    from octowright.browser_pool import driver_relaunch

    return driver_relaunch.relaunch_planned(session)


def _record_process_crash(session: Any, proc: BrowserProcess) -> None:
    """Route a dead browser process through the crash path, once."""
    from octowright.browser_pool.events import SessionCrashedEvent
    from octowright.browser_pool.session_event_bus import session_event_bus

    session._crashed = True
    evidence = _evidence(proc)
    recovering = _relaunch_scheduled(session)
    _PROCESS_CRASHED.add(1, attributes={"kind": session.kind})
    log.warning(
        "octowright.browser.process_crashed",
        instance_id=session.instance_id,
        kind=session.kind,
        profile=session.profile,
        pid=proc.pid,
        evidence=evidence,
        log_path=str(session.log_path),
    )
    session._process_crash_incident = incidents.record(
        incidents.CATEGORY_BROWSER_PROCESS_CRASH,
        instance_id=session.instance_id,
        kind=session.kind,
        url=session.url,
        pid=proc.pid,
        evidence=evidence,
        outcome="relaunching" if recovering else "lost",
        lost_downloads=0,
    )
    with contextlib.suppress(Exception):
        session.recorder.record("browser_crash", scope="process", evidence=evidence)
    with contextlib.suppress(Exception):
        session_event_bus.publish_nowait(
            SessionCrashedEvent(
                instance_id=session.instance_id,
                kind=session.kind,
                label=session.label,
                profile=session.profile,
                scope="process",
                log_path=str(session.log_path),
                recovering=recovering,
            )
        )


def classify_external_close(session: Any) -> SessionCloseReason:
    """The close reason for an external close signal on a still-live session.

    Call it only for the session's current identity (the listener checks), at
    the FIRST evicting signal -- that timing is what the liveness rule measures.
    Publishes the verdict for :func:`wait_for_exit_verdict`.
    """
    if getattr(session, "_exit_verdict", None) == "crashed" or getattr(session, "_crashed", False):
        verdict: ExitVerdict = "crashed"
    else:
        proc = getattr(session, "_browser_process", None)
        measured = exit_verdict(proc)
        if measured == "crashed" and proc is not None:
            _record_process_crash(session, proc)
        verdict = measured or "closed"
    session._exit_verdict = verdict
    event = getattr(session, "_exit_verdict_event", None)
    if event is not None:
        event.set()
    return "crashed" if verdict == "crashed" else "user_close"


async def wait_for_exit_verdict(session: Any, *, timeout: float) -> ExitVerdict | None:
    """The eviction's verdict, or ``None`` if the session was not evicted in time.

    For a caller that saw a ``TargetClosedError`` a beat BEFORE the close
    signal. It must not sample liveness itself: on a Firefox user close the
    rejection arrives after the process has already exited cleanly.
    """
    event = session._exit_verdict_event
    if not event.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout)
    verdict = session._exit_verdict
    return verdict if verdict in ("crashed", "closed") else None


__all__ = [
    "BrowserProcess",
    "ExitVerdict",
    "classify_external_close",
    "exit_verdict",
    "find_browser_process",
    "process_state",
    "wait_for_exit_verdict",
]
