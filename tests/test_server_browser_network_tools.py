# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from octowright.server.browser import network as _network


@pytest.fixture(autouse=True)
def _patch_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
    fake_pool = MagicMock()
    monkeypatch.setattr(_network, "pool", fake_pool)
    return fake_pool


def test_browser_network_summary_aggregates_without_raw_dump(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.get_network_requests.return_value = {
        "requests": [
            {
                "url": "https://example.com/",
                "method": "GET",
                "resource_type": "document",
                "status": 200,
                "status_text": "OK",
            },
            {
                "url": "https://example.com/api/users",
                "method": "POST",
                "resource_type": "xhr",
                "status": 500,
                "status_text": "Server Error",
            },
            {
                "url": "https://cdn.example.com/app.js",
                "method": "GET",
                "resource_type": "script",
                "status": 200,
                "status_text": "OK",
            },
            {
                "url": "https://api.example.com/slow",
                "method": "GET",
                "resource_type": "fetch",
                "status": None,
                "failure": "net::ERR_TIMED_OUT",
            },
        ],
        "next_cursor": 9,
        "total": 4,
        "total_retained": 4,
        "dropped": 5,
    }
    _patch_pool.get.return_value = session

    out = _network.browser_network_summary("i", failure_limit=1)

    assert out["total"] == 4
    assert out["dropped"] == 5
    assert out["next_cursor"] == 9
    assert out["by_status"] == [{"key": "200", "count": 2}, {"key": "500", "count": 1}, {"key": "failed", "count": 1}]
    assert out["by_resource_type"] == [
        {"key": "document", "count": 1},
        {"key": "fetch", "count": 1},
        {"key": "script", "count": 1},
        {"key": "xhr", "count": 1},
    ]
    assert out["by_method"] == [{"key": "GET", "count": 3}, {"key": "POST", "count": 1}]
    assert out["by_host"][0] == {"key": "example.com", "count": 2}
    assert out["count"] == 4
    assert out["host_count"] == 3
    assert out["ok_count"] == 2
    assert out["redirect_count"] == 0
    assert out["http_error_count"] == 1
    assert out["network_error_count"] == 1
    assert out["has_failures"] is True
    assert out["by_status_class"] == [
        {"key": "2xx", "count": 2},
        {"key": "5xx", "count": 1},
        {"key": "failed", "count": 1},
    ]
    assert out["problem_hosts"] == [
        {
            "host": "api.example.com",
            "total": 1,
            "failure_count": 1,
            "http_error_count": 0,
            "network_error_count": 1,
            "statuses": [{"key": "failed", "count": 1}],
            "action": {"tool": "browser_network_summary", "args": {"instance_id": "i", "url": "api.example.com"}},
        },
        {
            "host": "example.com",
            "total": 2,
            "failure_count": 1,
            "http_error_count": 1,
            "network_error_count": 0,
            "statuses": [{"key": "200", "count": 1}, {"key": "500", "count": 1}],
            "action": {"tool": "browser_network_summary", "args": {"instance_id": "i", "url": "example.com"}},
        },
    ]
    assert out["failure_count"] == 2
    assert out["failures"] == [
        {
            "url": "https://example.com/api/users",
            "host": "example.com",
            "path": "/api/users",
            "method": "POST",
            "resource_type": "xhr",
            "status": 500,
            "status_class": "5xx",
            "status_text": "Server Error",
            "action": {
                "tool": "browser_network_summary",
                "args": {"instance_id": "i", "url": "https://example.com/api/users"},
            },
        }
    ]
    assert out["next_actions"] == [
        {"tool": "browser_network_summary", "args": {"instance_id": "i", "since": 9}},
        {"tool": "browser_network_summary", "args": {"instance_id": "i", "url": "<url-or-host>"}},
        {"tool": "capture_create", "args": {"instance_id": "i", "source": "network", "response_mode": "summary"}},
    ]
    assert "requests" not in out


def test_browser_network_requests_forwards_filters_and_cursor(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.get_network_requests.return_value = {"requests": [], "next_cursor": 7, "total": 0}
    _patch_pool.get.return_value = session

    out = _network.browser_network_requests(
        "i",
        url="/api",
        method="post",
        resource_type="xhr",
        since=4,
    )

    assert out["next_cursor"] == 7
    session.get_network_requests.assert_called_once_with(
        url_filter="/api",
        method_filter="post",
        resource_type_filter="xhr",
        since=4,
        include_headers=False,
        limit=_network.NETWORK_REQUESTS_DEFAULT_LIMIT,
    )


def test_browser_network_requests_summary_mode_returns_compact_summary(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.get_network_requests.return_value = {
        "requests": [
            {
                "url": "https://example.com/api",
                "method": "GET",
                "resource_type": "fetch",
                "status": 500,
            }
        ],
        "next_cursor": 12,
        "total": 1,
    }
    _patch_pool.get.return_value = session

    out = _network.browser_network_requests(
        "i",
        url="/api",
        method="GET",
        resource_type="fetch",
        since=3,
        response_mode="summary",
    )

    assert "requests" not in out
    assert out["failure_count"] == 1
    assert out["next_cursor"] == 12
    session.get_network_requests.assert_called_once_with(
        url_filter="/api",
        method_filter="GET",
        resource_type_filter="fetch",
        since=3,
        include_headers=False,
        limit=None,
    )


def test_top_level_server_exports_browser_network_summary() -> None:
    from octowright import server

    assert hasattr(server, "browser_network_summary")


# ─── header opt-in + row cap ───────────────────────────────────────────────


def _limit_passed(session: MagicMock) -> int | None:
    return session.get_network_requests.call_args.kwargs["limit"]


def test_headers_are_withheld_unless_the_caller_asks(_patch_pool: MagicMock) -> None:
    """A recorded header map is ~7x the JSON size of the row it rides on, and is
    near-identical boilerplate on every row -- always-on it turned an unfiltered
    read of an ordinary page from ~6.6k tokens into ~45k."""
    session = MagicMock()
    session.get_network_requests.return_value = {"requests": [], "next_cursor": 0, "total": 0}
    _patch_pool.get.return_value = session

    _network.browser_network_requests("i")

    assert session.get_network_requests.call_args.kwargs["include_headers"] is False


def test_headers_are_forwarded_when_the_caller_opts_in(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.get_network_requests.return_value = {"requests": [], "next_cursor": 0, "total": 0}
    _patch_pool.get.return_value = session

    _network.browser_network_requests("i", include_headers=True)

    assert session.get_network_requests.call_args.kwargs["include_headers"] is True


def test_an_unbounded_read_is_capped_by_default(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.get_network_requests.return_value = {"requests": [], "next_cursor": 0, "total": 0}
    _patch_pool.get.return_value = session

    _network.browser_network_requests("i")

    assert _limit_passed(session) == _network.NETWORK_REQUESTS_DEFAULT_LIMIT


def test_an_explicit_limit_is_honoured(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.get_network_requests.return_value = {"requests": [], "next_cursor": 0, "total": 0}
    _patch_pool.get.return_value = session

    _network.browser_network_requests("i", limit=5)

    assert _limit_passed(session) == 5


def test_an_oversized_limit_is_clamped(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.get_network_requests.return_value = {"requests": [], "next_cursor": 0, "total": 0}
    _patch_pool.get.return_value = session

    _network.browser_network_requests("i", limit=10_000)

    assert _limit_passed(session) == _network.NETWORK_REQUESTS_MAX_LIMIT


def test_a_non_positive_limit_falls_back_to_the_default(_patch_pool: MagicMock) -> None:
    """Zero/negative most plausibly means "no opinion", not "unbounded" -- and
    an LLM must not be able to remove the cap by passing 0."""
    session = MagicMock()
    session.get_network_requests.return_value = {"requests": [], "next_cursor": 0, "total": 0}
    _patch_pool.get.return_value = session

    _network.browser_network_requests("i", limit=0)

    assert _limit_passed(session) == _network.NETWORK_REQUESTS_DEFAULT_LIMIT


def test_the_summary_reads_every_row(_patch_pool: MagicMock) -> None:
    """The summary AGGREGATES -- a capped read would silently give wrong counts."""
    session = MagicMock()
    session.get_network_requests.return_value = {"requests": [], "next_cursor": 0, "total": 0}
    _patch_pool.get.return_value = session

    _network.browser_network_summary("i")

    assert _limit_passed(session) is None
    assert session.get_network_requests.call_args.kwargs["include_headers"] is False
