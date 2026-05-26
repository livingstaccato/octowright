# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Typed events emitted by the browser pool for in-process subscribers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SessionCloseReason = Literal["agent_close", "user_close", "external_disconnect", "shutdown"]


@dataclass(slots=True, frozen=True)
class SessionClosedEvent:
    """Published whenever a browser session leaves the pool.

    ``reason`` is a coarse signal from the pool's perspective:

    * ``agent_close`` — explicit ``browser_close`` / ``browser_close_all`` call
      from an MCP client.  The session was removed from the registry by
      ``close_browser`` before any Playwright close events fire.
    * ``user_close`` — the human closed the window or dismissed all pages;
      the pool was notified by Playwright's page/context/browser events.
      Playwright does not distinguish "user clicked X" from "browser OOM'd and
      the OS killed the window" at the event level, so ``user_close`` also
      covers unexpected crashes where the browser process exits cleanly enough
      to deliver a ``disconnected`` event.
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


__all__ = ["SessionCloseReason", "SessionClosedEvent"]
