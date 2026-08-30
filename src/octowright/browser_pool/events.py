# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Typed events emitted by the browser pool for in-process subscribers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SessionCloseReason = Literal["agent_close", "user_close", "external_disconnect", "crashed", "shutdown"]

# Where a crash happened. ``renderer`` is a Playwright ``page.on("crash")``
# (a tab/"Aw, Snap"); a renderer crash that also takes the browser process down
# additionally evicts the session with ``SessionClosedEvent(reason="crashed")``.
# "unresponsive" is neither -- the target is alive and simply stopped
# answering, which no Playwright event reports, so it is raised by the call
# budget in session/timeouts.py rather than observed.
CrashScope = Literal["renderer", "process", "unresponsive"]


@dataclass(slots=True, frozen=True)
class SessionClosedEvent:
    """Published whenever a browser session leaves the pool.

    ``reason`` is a coarse signal from the pool's perspective:

    * ``agent_close`` — explicit ``browser_close`` / ``browser_close_all`` call
      from an MCP client.  The session was removed from the registry by
      ``close_browser`` before any Playwright close events fire.
    * ``user_close`` — the human closed the window or dismissed all pages;
      the pool was notified by Playwright's page/context/browser events.
      The ``disconnected`` event alone does not distinguish "user clicked X"
      from "the process died", so ``user_close`` covers external closes where
      no crash was observed.
    * ``crashed`` — a Playwright ``page.on("crash")`` fired on this session
      before it was evicted (renderer crash that also brought the process down).
      The crash signal is what lets us upgrade an otherwise-ambiguous external
      disconnect to a definite crash; a proactive ``SessionCrashedEvent`` is
      also published the moment the crash is observed (see below).
    * ``external_disconnect`` — the browser process disappeared without emitting
      a clean close event (e.g. SIGKILL, OOM without an orderly Playwright
      teardown).  In practice Playwright delivers ``browser.disconnected`` even
      on hard kills on most platforms, so this reason is reserved for future
      use when a more reliable signal is available.  For now listener.py maps
      both "clean external close" and "hard disconnect" to ``user_close``.
    * ``shutdown`` — the pool is tearing down (``shutdown_pool`` / daemon exit).
      Emitted directly by ``shutdown_pool`` before ``close_all`` runs; the
      subsequent per-session ``close_browser`` calls will also emit
      ``agent_close`` events for any session that was not already gone.
    """

    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    reason: SessionCloseReason
    log_path: str


@dataclass(slots=True, frozen=True)
class SessionCrashedEvent:
    """Published the moment a browser crash is observed, before any eviction.

    Unlike :class:`SessionClosedEvent` this does NOT mean the session left the
    pool — a ``renderer`` crash leaves the browser process alive with a dead
    page. It is a proactive signal so an MCP client learns "this page crashed,
    reload it or relaunch the browser" immediately, rather than only when its
    next tool call on the dead page fails.
    """

    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    scope: CrashScope
    log_path: str
    # True when Octowright scheduled auto-recovery for this crash (it will replace
    # the dead page). Lets the client say "auto-recovering, hold" instead of the
    # stale "relaunch it now" — the authoritative outcome arrives as a
    # SessionRecoveredEvent. False when recovery is off/exhausted (relaunch IS needed).
    # Always False for scope="unresponsive": replacing the page is right for a
    # crash but wrong for a merely-slow target that may still be executing, so
    # an unresponsive target is never auto-recovered -- see session/timeouts.py.
    recovering: bool = False


# How a renderer-crash auto-recovery resolved.
RecoveryOutcome = Literal["recovered", "failed", "exhausted"]


@dataclass(slots=True, frozen=True)
class SessionRecoveredEvent:
    """Published when a renderer-crash auto-recovery resolves — the accurate
    follow-up to a ``SessionCrashedEvent(recovering=True)``.

    ``outcome``: ``recovered`` (a fresh page replaced the dead one in the same
    browser — usable again, no relaunch needed), ``failed`` (replacement failed,
    the browser process likely died — relaunch), or ``exhausted`` (the page keeps
    crashing past the recovery cap — relaunch / the page is unstable)."""

    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    outcome: RecoveryOutcome
    attempts: int
    log_path: str


@dataclass(slots=True, frozen=True)
class DriverDiedEvent:
    """Published when the shared Playwright driver dies and self-heals — every
    browser that rode it is gone at once. A proactive signal so the MCP client
    learns its sessions were lost immediately (and whether they're being
    auto-reopened), rather than only when its next tool call fails or it polls
    ``octowright_status``. The old→new id mapping lives in
    ``octowright_status().pool.lost_sessions``."""

    restart_count: int
    relaunch_mode: str  # off | new-id | keep-id
    lost_count: int
    lost_instance_ids: tuple[str, ...]


# Anything the session event bus may carry.
SessionEvent = SessionClosedEvent | SessionCrashedEvent | SessionRecoveredEvent | DriverDiedEvent

__all__ = [
    "CrashScope",
    "DriverDiedEvent",
    "RecoveryOutcome",
    "SessionCloseReason",
    "SessionClosedEvent",
    "SessionCrashedEvent",
    "SessionEvent",
    "SessionRecoveredEvent",
]
