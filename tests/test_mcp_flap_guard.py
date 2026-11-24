# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader-side storm defenses: new-session rate limit + session-table cap.

Covers ``http/mcp_flap_guard`` (limiter, request classification, source keying,
eviction selection, 429 middleware, knob parsing) and the housekeeping
``_enforce_mcp_session_cap_once`` eviction job.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.http import mcp_flap_guard as fg

# ── NewSessionRateLimiter ────────────────────────────────────────────────────


def test_limiter_admits_up_to_max_then_refuses() -> None:
    lim = fg.NewSessionRateLimiter(max_events=3, window_seconds=100.0)
    assert [lim.allow("a", now=t) for t in (0.0, 0.1, 0.2)] == [True, True, True]
    assert lim.allow("a", now=0.3) is False  # 4th within window


def test_limiter_refused_event_not_recorded_so_backoff_recovers() -> None:
    lim = fg.NewSessionRateLimiter(max_events=1, window_seconds=10.0)
    assert lim.allow("a", now=0.0) is True
    assert lim.allow("a", now=1.0) is False  # refused, NOT recorded
    # After the first event ages out of the window, the source recovers.
    assert lim.allow("a", now=10.5) is True


def test_limiter_is_per_source() -> None:
    lim = fg.NewSessionRateLimiter(max_events=1, window_seconds=100.0)
    assert lim.allow("a", now=0.0) is True
    assert lim.allow("a", now=0.1) is False
    assert lim.allow("b", now=0.1) is True  # different bucket unaffected


def test_limiter_prune_drops_emptied_keys() -> None:
    lim = fg.NewSessionRateLimiter(max_events=2, window_seconds=5.0)
    lim.allow("a", now=0.0)
    lim.allow("b", now=0.0)
    lim.prune(now=100.0)
    assert lim._events == {}


# ── request classification + source keying ───────────────────────────────────


def _scope(
    method: str, headers: list[tuple[bytes, bytes]] | None = None, client: tuple[str, int] | None = None
) -> dict:
    scope: dict[str, Any] = {"type": "http", "method": method, "headers": headers or []}
    if client is not None:
        scope["client"] = client
    return scope


def test_is_new_session_request_post_without_id() -> None:
    assert fg.is_new_session_request(_scope("POST")) is True


def test_is_not_new_session_when_id_present() -> None:
    assert fg.is_new_session_request(_scope("POST", [(b"mcp-session-id", b"abc")])) is False


def test_is_not_new_session_for_delete_or_get() -> None:
    assert fg.is_new_session_request(_scope("DELETE")) is False
    assert fg.is_new_session_request(_scope("GET")) is False


def test_source_key_uses_follower_header() -> None:
    assert fg.source_key(_scope("POST", [(b"x-octowright-follower", b"4321")])) == "4321"


def test_source_key_anonymous_without_header() -> None:
    assert fg.source_key(_scope("POST")) == fg._ANONYMOUS_SOURCE


def test_source_key_anonymous_on_bad_encoding_or_empty() -> None:
    assert fg.source_key(_scope("POST", [(b"x-octowright-follower", b"\xff\xfe")])) == fg._ANONYMOUS_SOURCE
    assert fg.source_key(_scope("POST", [(b"x-octowright-follower", b"  ")])) == fg._ANONYMOUS_SOURCE


def test_source_key_headerless_shares_the_one_anonymous_bucket_whatever_the_peer() -> None:
    """A headerless storm opens a new TCP connection per session (a fresh
    ephemeral port each time); keyed by peer, every request got its own empty
    bucket and the storm was never throttled. Every headerless request shares
    the one ``anonymous`` bucket -- the storm, collectively throttled."""
    a = fg.source_key(_scope("POST", client=("127.0.0.1", 111)))
    b = fg.source_key(_scope("POST", client=("127.0.0.1", 222)))
    assert a == b == fg._ANONYMOUS_SOURCE


def test_source_key_bad_header_falls_back_to_anonymous_even_with_peer() -> None:
    key = fg.source_key(_scope("POST", [(b"x-octowright-follower", b"  ")], client=("127.0.0.1", 999)))
    assert key == fg._ANONYMOUS_SOURCE


@pytest.mark.anyio
async def test_headerless_storm_on_fresh_connections_is_throttled() -> None:
    lim = fg.NewSessionRateLimiter(max_events=3, window_seconds=100.0)
    mw = fg.McpNewSessionRateLimitMiddleware(app=None, limiter=lim, retry_after=10.0)
    admitted = 0
    for port in range(40000, 40020):
        _sent, called = await _drive(mw, _scope("POST", client=("127.0.0.1", port)))
        admitted += len(called)
    assert admitted == 3


# ── select_eviction_victims ──────────────────────────────────────────────────


def test_evict_none_when_not_over() -> None:
    assert fg.select_eviction_victims(["a", "b"], {"a", "b"}, {}, over=0) == []
    assert fg.select_eviction_victims(["a", "b"], {"a", "b"}, {}, over=-1) == []


def test_evict_abandoned_first_newest_created() -> None:
    # instance order = creation order; s1,s2 abandoned (not recent), s3,s4 active.
    victims = fg.select_eviction_victims(["s1", "s2", "s3", "s4"], recent_ids={"s3", "s4"}, last_seen={}, over=2)
    assert victims == ["s2", "s1"]
    # Only one needed: the newest abandoned one goes, the oldest survives.
    assert fg.select_eviction_victims(["s1", "s2", "s3"], recent_ids=set(), last_seen={}, over=1) == ["s3"]


def test_evict_quiet_live_follower_survives_a_storm() -> None:
    # "real" is the oldest session and silent past the TTL (not recent), but a
    # live follower names it; the storm's fresh sessions are all recent.
    storm = [f"storm{i}" for i in range(6)]
    victims = fg.select_eviction_victims(
        ["real", *storm],
        recent_ids=set(storm),
        last_seen={s: float(i) for i, s in enumerate(storm)},
        over=4,
        protected_ids={"real"},
    )
    assert "real" not in victims
    assert victims == storm[:4]  # least-recently-seen storm sessions first


def test_evict_unprotected_abandoned_before_unprotected_recent() -> None:
    victims = fg.select_eviction_victims(
        ["p", "a1", "r1", "a2"],
        recent_ids={"r1"},
        last_seen={"r1": 1.0},
        over=3,
        protected_ids={"p"},
    )
    assert victims == ["a2", "a1", "r1"]


def test_evict_protected_when_they_alone_exceed_cap_most_idle_first() -> None:
    # The cap stays a hard bound: protected sessions are evicted once nothing
    # else is left -- silent ones first, then least-recently-seen.
    victims = fg.select_eviction_victims(
        ["p1", "p2", "p3", "u"],
        recent_ids={"p1", "p2"},
        last_seen={"p1": 9.0, "p2": 3.0},
        over=3,
        protected_ids={"p1", "p2", "p3"},
    )
    assert victims == ["u", "p3", "p2"]


def test_evict_falls_through_to_least_recently_seen_active() -> None:
    # Only one abandoned (s1); need 2 → also evict the least-recently-seen active.
    victims = fg.select_eviction_victims(
        ["s1", "s2", "s3"],
        recent_ids={"s2", "s3"},
        last_seen={"s2": 50.0, "s3": 10.0},  # s3 more idle
        over=2,
    )
    assert victims == ["s1", "s3"]


# ── knob parsing ─────────────────────────────────────────────────────────────


def test_max_sessions_default_on() -> None:
    assert fg.mcp_max_sessions(raw=None) == fg._MAX_SESSIONS_DEFAULT


@pytest.mark.parametrize("token", ["off", "0", "never", "none", "disabled", "false", "no"])
def test_max_sessions_disable_tokens(token: str) -> None:
    assert fg.mcp_max_sessions(raw=token) is None


def test_max_sessions_custom_and_bad(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MCP_MAX_SESSIONS", "42")
    assert fg.mcp_max_sessions() == 42
    monkeypatch.setenv("OCTOWRIGHT_MCP_MAX_SESSIONS", "garbage")
    assert fg.mcp_max_sessions() == fg._MAX_SESSIONS_DEFAULT  # falls back to default


def test_new_session_rate_default_and_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert fg.mcp_new_session_rate() == (fg._NEW_SESSION_MAX_DEFAULT, fg._NEW_SESSION_WINDOW_DEFAULT)
    monkeypatch.setenv("OCTOWRIGHT_MCP_NEW_SESSION_MAX", "off")
    assert fg.mcp_new_session_rate() is None


# ── McpNewSessionRateLimitMiddleware ─────────────────────────────────────────


async def _drive(mw: Any, scope: dict) -> tuple[list[dict], list[bool]]:
    sent: list[dict] = []
    called: list[bool] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    # Replace inner app with a recorder so we can tell passthrough from rejection.
    mw.app = lambda s, r, se: called.append(True) or _noop(se)
    await mw(scope, receive, send)
    return sent, called


async def _noop(send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.anyio
async def test_middleware_429s_over_rate_new_session() -> None:
    lim = fg.NewSessionRateLimiter(max_events=1, window_seconds=100.0)
    mw = fg.McpNewSessionRateLimitMiddleware(app=None, limiter=lim, retry_after=10.0)
    # First new-session passes through.
    _sent1, called1 = await _drive(mw, _scope("POST"))
    assert called1 == [True]
    # Second new-session from same (anonymous) source is throttled → 429, app not called.
    sent2, called2 = await _drive(mw, _scope("POST"))
    assert called2 == []
    start = next(m for m in sent2 if m["type"] == "http.response.start")
    assert start["status"] == 429
    assert (b"retry-after", b"10") in start["headers"]


@pytest.mark.anyio
async def test_middleware_passes_continuation_requests_even_over_rate() -> None:
    lim = fg.NewSessionRateLimiter(max_events=0, window_seconds=100.0)  # everything would be over
    mw = fg.McpNewSessionRateLimitMiddleware(app=None, limiter=lim, retry_after=5.0)
    # A request carrying a session id is NOT a creation → never throttled.
    _sent, called = await _drive(mw, _scope("POST", [(b"mcp-session-id", b"live")]))
    assert called == [True]


# ── housekeeping cap-eviction job ────────────────────────────────────────────


class _FakeTransport:
    def __init__(self) -> None:
        self.is_terminated = False

    async def terminate(self) -> None:
        self.is_terminated = True


class _FakeManager:
    def __init__(self, session_ids: list[str]) -> None:
        self._server_instances = {sid: _FakeTransport() for sid in session_ids}


@pytest.mark.anyio
async def test_cap_job_evicts_abandoned_down_to_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping
    from octowright.http import app as http_app
    from octowright.http.mcp_session_tracker import McpSessionTracker

    monkeypatch.setenv("OCTOWRIGHT_MCP_MAX_SESSIONS", "2")
    manager = _FakeManager(["s1", "s2", "s3", "s4"])
    monkeypatch.setattr("octowright.http.app.mcp_session_manager", lambda _mcp_server: manager, raising=False)
    # s3,s4 are recently active; s1,s2 abandoned.
    tracker = McpSessionTracker()
    tracker.mark_active("s3")
    tracker.mark_active("s4")
    monkeypatch.setattr(http_app, "_session_tracker", tracker)

    await housekeeping._enforce_mcp_session_cap_once(log=_QuietLog())

    remaining = set(manager._server_instances)
    assert remaining == {"s3", "s4"}  # abandoned evicted, active kept


def _record_follower(path: Any, pid: int, session_id: str) -> None:
    from octowright import bridge_state

    bridge_state.record_snapshot(
        path=path,
        follower_pid=pid,
        remote_url="http://127.0.0.1/mcp/",
        remote_session_id=session_id,
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )


@pytest.mark.anyio
async def test_cap_job_keeps_quiet_live_follower_session_over_storm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import os

    from octowright import defaults, housekeeping
    from octowright.http import app as http_app
    from octowright.http.mcp_session_tracker import McpSessionTracker

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    _record_follower(path, os.getpid(), "real")  # a live follower names "real"
    _record_follower(path, 1_000_000_000, "ghost")  # a dead one protects nothing

    monkeypatch.setenv("OCTOWRIGHT_MCP_MAX_SESSIONS", "3")
    storm = [f"storm{i}" for i in range(4)]
    manager = _FakeManager(["real", "ghost", *storm])
    monkeypatch.setattr("octowright.http.app.mcp_session_manager", lambda _mcp_server: manager, raising=False)
    tracker = McpSessionTracker()  # "real" is silent past the TTL; the storm is fresh
    for sid in storm:
        tracker.mark_active(sid)
    monkeypatch.setattr(http_app, "_session_tracker", tracker)

    await housekeeping._enforce_mcp_session_cap_once(log=_QuietLog())

    remaining = set(manager._server_instances)
    assert len(remaining) == 3
    assert "real" in remaining
    assert "ghost" not in remaining


@pytest.mark.anyio
async def test_cap_job_still_bounds_and_logs_when_live_followers_exceed_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import os
    from unittest.mock import MagicMock

    from octowright import defaults, housekeeping

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    _record_follower(path, os.getpid(), "p1")
    _record_follower(path, os.getppid(), "p2")

    monkeypatch.setenv("OCTOWRIGHT_MCP_MAX_SESSIONS", "1")
    manager = _FakeManager(["p1", "p2"])
    monkeypatch.setattr("octowright.http.app.mcp_session_manager", lambda _mcp_server: manager, raising=False)
    log = MagicMock()

    await housekeeping._enforce_mcp_session_cap_once(log=log)

    assert len(manager._server_instances) == 1
    events = [c.args[0] for c in log.warning.call_args_list]
    assert "octowright.housekeeping.evicted_live_follower_sessions" in events


@pytest.mark.anyio
async def test_cap_job_noop_under_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping

    monkeypatch.setenv("OCTOWRIGHT_MCP_MAX_SESSIONS", "10")
    manager = _FakeManager(["s1", "s2"])
    monkeypatch.setattr("octowright.http.app.mcp_session_manager", lambda _mcp_server: manager, raising=False)
    await housekeeping._enforce_mcp_session_cap_once(log=_QuietLog())
    assert set(manager._server_instances) == {"s1", "s2"}


@pytest.mark.anyio
async def test_cap_job_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping

    monkeypatch.setenv("OCTOWRIGHT_MCP_MAX_SESSIONS", "off")
    manager = _FakeManager(["s1", "s2", "s3"])
    monkeypatch.setattr("octowright.http.app.mcp_session_manager", lambda _mcp_server: manager, raising=False)
    await housekeeping._enforce_mcp_session_cap_once(log=_QuietLog())
    assert len(manager._server_instances) == 3  # untouched


class _QuietLog:
    def warning(self, *a: Any, **k: Any) -> None:
        pass

    def info(self, *a: Any, **k: Any) -> None:
        pass
