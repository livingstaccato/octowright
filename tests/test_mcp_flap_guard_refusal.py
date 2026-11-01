# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The leader's sustained-throttle tier: pacing (429) escalates to an honest
refusal (410) once one source's rejections span the refusal window, and the
logging around both tiers stays at one line per source per window.

A follower too old for the leader's handshake trips the new-session limit on
every reconnect, forever — pacing it only burns the leader, and refusing it
immediately would 410 a bursty but legitimate client. These pin the crossing:
short bursts keep the 429, whole-window loops turn into a named 410, quiet
sources are forgiven, and the kill switch restores pure pacing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from octowright.http import mcp_flap_guard as fg

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _scope(*, with_session: bool = False) -> dict[str, Any]:
    headers = [(b"x-octowright-follower", b"4321")]
    if with_session:
        headers.append((b"mcp-session-id", b"abc"))
    return {"type": "http", "method": "POST", "headers": headers}


async def _drive(mw: fg.McpNewSessionRateLimitMiddleware, scope: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    """One request through the middleware: (status, parsed 4xx body); (None, {})
    when it reached the app."""
    sent: list[dict[str, Any]] = []
    reached: list[dict[str, Any]] = []

    async def app(scope: dict, receive: Any, send: Any) -> None:
        reached.append(scope)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    mw.app = app
    await mw(scope, None, send)
    if reached:
        return None, {}
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = next(m for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(body["body"])


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    def debug(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))


def _patch_clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(fg.time, "monotonic", lambda: clock["now"])
    return clock


def _middleware(**kwargs: Any) -> fg.McpNewSessionRateLimitMiddleware:
    limiter = fg.NewSessionRateLimiter(
        max_events=kwargs.pop("max_events", 1), window_seconds=kwargs.pop("window_seconds", 10.0)
    )
    defaults = {"retry_after": 10.0, "refusal_span": 20.0}
    defaults.update(kwargs)
    return fg.McpNewSessionRateLimitMiddleware(app=None, limiter=limiter, **defaults)


async def _statuses(
    mw: fg.McpNewSessionRateLimitMiddleware, clock: dict[str, float], schedule: list[float]
) -> list[int | None]:
    out = []
    for t in schedule:
        clock["now"] = t
        status, _ = await _drive(mw, _scope())
        out.append(status)
    return out


async def test_sustained_throttling_crosses_to_410(monkeypatch: pytest.MonkeyPatch) -> None:
    log = _Log()
    monkeypatch.setattr(fg, "log", log)
    clock = _patch_clock(monkeypatch)
    # A 100s limiter window keeps every scheduled request a rejection after the
    # first, so the refusal tracker's span math is under exact test control.
    mw = _middleware(max_events=1, window_seconds=100.0)
    # Six rejections spanning 5s is still a burst: the 20s span is not crossed.
    statuses = await _statuses(mw, clock, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert statuses == [None, 429, 429, 429, 429, 429, 429]
    # The loop held across the whole 20s refusal span → the next rejection is
    # a refusal, and it stays refused while it keeps coming.
    statuses = await _statuses(mw, clock, [21.0, 22.0])
    assert statuses == [410, 410]
    refused = [e for e in log.events if e[0] == "octowright.mcp.sustained_throttle_refused"]
    assert len(refused) == 1, "the refusal logs once per sustained episode, not per request"
    assert refused[0][1]["source"] == "4321"


async def test_quiet_source_is_forgiven(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _patch_clock(monkeypatch)
    mw = _middleware()
    # Limiter window 10s, so a pause re-admits once (the None at t=21): the
    # rejection stream STILL spans the refusal window through the pause, which
    # is the point — sustained means the rejections, not the pauses.
    statuses = await _statuses(mw, clock, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 21.0, 22.0])
    assert statuses == [None, 429, 429, 429, 429, 429, 429, None, 410]
    # Far past the refusal span with no traffic: the loop's debt is paid.
    statuses = await _statuses(mw, clock, [100.0, 100.5])
    assert statuses == [None, 429]


async def test_refusal_disabled_is_pure_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _patch_clock(monkeypatch)
    mw = _middleware(refusal_span=None)
    statuses = await _statuses(mw, clock, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 25.0, 26.0])
    assert set(statuses) == {None, 429}


async def test_410_body_names_the_reconnect_step(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _patch_clock(monkeypatch)
    mw = _middleware(max_events=1, window_seconds=100.0)
    await _statuses(mw, clock, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    clock["now"] = 21.0
    status, body = await _drive(mw, _scope())
    assert status == 410
    assert "too old or" in body["error"]
    assert "reconnect your MCP client" in body["error"]
    assert body["source"] == "4321"
    # Pacing still applies: an old follower ignores the status but honors the header.
    assert body["retry_after_seconds"] == 10


async def test_429_body_carries_source_and_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_clock(monkeypatch)
    mw = _middleware()
    clock_status, _ = await _drive(mw, _scope())
    assert clock_status is None
    status, body = await _drive(mw, _scope())
    assert status == 429
    assert body == {
        "error": "new-session rate limit exceeded; retry after backoff",
        "source": "4321",
        "retry_after_seconds": 10,
    }


async def test_throttle_warning_is_one_per_source_per_window(monkeypatch: pytest.MonkeyPatch) -> None:
    log = _Log()
    monkeypatch.setattr(fg, "log", log)
    clock = _patch_clock(monkeypatch)
    mw = _middleware()
    await _statuses(mw, clock, [0.0, 1.0, 2.0, 3.0])
    await _statuses(mw, clock, [11.0, 12.0])
    lines = [e for e in log.events if e[0] == "octowright.mcp.new_session_throttled"]
    assert len(lines) == 2
    first, second = lines[0][1], lines[1][1]
    assert first["source"] == "4321"
    assert first["throttled_since_last_log"] == 1
    assert first["requests_in_window"] == 1
    # t=11 is the second emitted line; it covers t=2, t=3 and t=11 since t=1's.
    assert second["throttled_since_last_log"] == 3


async def test_continuations_are_never_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_clock(monkeypatch)
    mw = _middleware()
    statuses = [status for status, _ in [await _drive(mw, _scope(with_session=True)) for _ in range(30)]]
    assert statuses == [None] * 30
