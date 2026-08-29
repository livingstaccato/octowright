# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A wedged target must reach the agent through the crash taxonomy.

`page.on("crash")` is silent for an unresponsive target, so without this the
only signal is a raw error string and the agent cannot tell "relaunch this
session" from "the transport died".

Covers:
- ``scope="unresponsive"`` is a valid ``CrashScope`` (Task 2, Step 1).
- A ``SessionCallTimeoutError`` raised inside a gated session operation
  publishes exactly one ``SessionCrashedEvent(scope="unresponsive")`` on the
  pool's event bus (Task 2, Step 3), via ``SessionOperationGate``'s
  ``on_call_timeout`` hook, wired by ``BrowserSession.__post_init__`` to
  ``BrowserSession._notify_call_timeout``.
- Nesting does not multiply the notification: only the ROOT gated operation's
  release fires the hook (``_LeaseToken.is_root``), so a timeout raised deep
  inside several reentrant ``session.operation(...)`` frames still publishes
  exactly once.
- An ordinary exception (not ``SessionCallTimeoutError``) never publishes —
  the hook is specific to the call-budget timeout, not "any gated error".
- The published event never sets ``recovering=True`` — an unresponsive
  target is deliberately never auto-recovered (see ``session/timeouts.py``
  and ``session/core.py``'s ``_notify_call_timeout`` docstring).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from octowright.browser_pool.events import SessionCrashedEvent
from octowright.browser_pool.session_event_bus import session_event_bus
from octowright.session import BrowserSession
from octowright.session.timeouts import SessionCallTimeoutError

# Bound for "assert nothing else arrives" checks below -- long enough that a
# genuine (buggy) second publish would reliably be observed, short enough
# that a correct run doesn't slow the suite down waiting it out.
_NOTHING_ELSE_ARRIVES_SECONDS = 0.2


def test_unresponsive_is_a_valid_crash_scope() -> None:
    event = SessionCrashedEvent(
        instance_id="abc123",  # pragma: allowlist secret (fake instance id)
        kind="webkit",
        label=None,
        profile=None,
        scope="unresponsive",
        log_path="/tmp/x.jsonl",
    )
    assert event.scope == "unresponsive"


@pytest.fixture
def fake_session_kwargs(tmp_path: Path) -> dict[str, object]:
    return {
        "instance_id": "wedged-session",
        "kind": "webkit",
        "label": "test-label",
        "url": "https://octowright.com",
        "browser": None,
        "context": MagicMock(),
        "page": MagicMock(),
        "recorder": MagicMock(),
        "log_path": tmp_path / "wedged.jsonl",
        "profile": "test-persona",
    }


async def test_call_timeout_publishes_unresponsive_event(fake_session_kwargs: dict[str, object]) -> None:
    """A ``SessionCallTimeoutError`` raised inside a gated session operation
    publishes exactly one ``SessionCrashedEvent(scope="unresponsive")``."""
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]

    async with session_event_bus.subscribe() as sub:
        with pytest.raises(SessionCallTimeoutError):
            async with session.operation("browser_evaluate"):
                raise SessionCallTimeoutError("browser_evaluate did not answer within 30.0s")

        received = await asyncio.wait_for(sub.get(), timeout=1.0)

    assert isinstance(received, SessionCrashedEvent)
    assert received.scope == "unresponsive"
    assert received.instance_id == "wedged-session"
    assert received.kind == "webkit"
    assert received.label == "test-label"
    assert received.profile == "test-persona"
    assert received.recovering is False
    assert received.log_path == str(fake_session_kwargs["log_path"])


async def test_nested_gated_operation_publishes_exactly_once(fake_session_kwargs: dict[str, object]) -> None:
    """A timeout raised inside a REENTRANT (nested) gated operation still
    surfaces through the outer lease, but must publish only once — not once
    per ``session.operation(...)`` frame it propagates through."""
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]

    async def _inner() -> None:
        # Reentrant: same task already owns the gate via the outer
        # `session.operation("macro_run")` below, so this does not queue.
        async with session.operation("macro_check"):
            raise SessionCallTimeoutError("macro_check did not answer within 30.0s")

    async with session_event_bus.subscribe() as sub:
        with pytest.raises(SessionCallTimeoutError):
            async with session.operation("macro_run"):
                await _inner()

        received = await asyncio.wait_for(sub.get(), timeout=1.0)
        assert received.scope == "unresponsive"

        # The nested (non-root) frame must not have published a second event.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(sub.get(), timeout=_NOTHING_ELSE_ARRIVES_SECONDS)


async def test_ordinary_error_does_not_publish_unresponsive_event(fake_session_kwargs: dict[str, object]) -> None:
    """Only ``SessionCallTimeoutError`` triggers the hook — any other
    exception propagating out of a gated operation must not publish."""
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]

    async with session_event_bus.subscribe() as sub:
        with pytest.raises(RuntimeError, match="boom"):
            async with session.operation("browser_click"):
                raise RuntimeError("boom")

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(sub.get(), timeout=_NOTHING_ELSE_ARRIVES_SECONDS)
