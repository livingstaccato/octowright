# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.session.core_network_mixin.

Covers _handle_response/_handle_request_failed wiring, the bounded
deque drop counter, and get_network_requests filter + cursor logic.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Any

from octowright.session.core_network_mixin import (
    SessionNetworkMixin,
    _matches_method,
    _matches_resource_type,
    _matches_url,
)


def _make_subject(maxlen: int = 100) -> SessionNetworkMixin:
    """Build a bare SessionNetworkMixin with the deque + counter set up."""
    subj = SessionNetworkMixin.__new__(SessionNetworkMixin)
    subj._network_requests = deque(maxlen=maxlen)
    subj._network_requests_dropped = 0
    return subj


# ─── filter predicate helpers ────────────────────────────────────────────────


class TestMatchUrl:
    def test_substring_match(self) -> None:
        """`in` substring match — partial URL matches."""
        assert _matches_url("login")({"url": "https://octowright.com/login?x=1"}) is True

    def test_no_match(self) -> None:
        """No substring → False."""
        assert _matches_url("missing")({"url": "https://octowright.com/x"}) is False

    def test_missing_url_field_returns_false(self) -> None:
        """`get('url', '')` default — empty string never contains anything (almost)."""
        assert _matches_url("anything")({}) is False

    def test_empty_filter_matches_everything(self) -> None:
        """Empty string is in every string — but the call site filters out empty."""
        assert _matches_url("")({"url": "https://x"}) is True


class TestMatchMethod:
    def test_case_insensitive(self) -> None:
        """Both filter and request method uppercased before compare."""
        assert _matches_method("post")({"method": "POST"}) is True
        assert _matches_method("POST")({"method": "post"}) is True

    def test_no_match(self) -> None:
        """Different methods → False."""
        assert _matches_method("GET")({"method": "POST"}) is False

    def test_missing_method_returns_false(self) -> None:
        """`get('method', '')` default — '' never matches the filter target."""
        assert _matches_method("GET")({}) is False


class TestMatchResourceType:
    def test_exact_match(self) -> None:
        """Equality (no case-folding for resource_type)."""
        assert _matches_resource_type("xhr")({"resource_type": "xhr"}) is True

    def test_no_match(self) -> None:
        """Different type → False."""
        assert _matches_resource_type("xhr")({"resource_type": "image"}) is False

    def test_case_sensitive(self) -> None:
        """Resource-type comparison is case-sensitive (Playwright uses lowercase)."""
        assert _matches_resource_type("xhr")({"resource_type": "XHR"}) is False


# ─── _handle_response ────────────────────────────────────────────────────────


class TestHandleResponse:
    def test_appends_response_record(self) -> None:
        """Response → record with url/method/resource_type/status/status_text."""
        subj = _make_subject()
        request = SimpleNamespace(url="https://x/api", method="GET", resource_type="fetch")
        response = SimpleNamespace(request=request, status=200, status_text="OK")
        subj._handle_response(response)
        assert len(subj._network_requests) == 1
        record = subj._network_requests[0]
        assert record == {
            "url": "https://x/api",
            "method": "GET",
            "resource_type": "fetch",
            "status": 200,
            "status_text": "OK",
            # Header capture: these records carried none, which made every
            # header feature unverifiable from the tool surface. The stub
            # request exposes no headers, so the scrubber yields {}.
            "headers": {},
        }

    def test_response_record_does_not_set_failure(self) -> None:
        """Successful response records have NO 'failure' key."""
        subj = _make_subject()
        request = SimpleNamespace(url="https://x", method="GET", resource_type="document")
        response = SimpleNamespace(request=request, status=200, status_text="OK")
        subj._handle_response(response)
        assert "failure" not in subj._network_requests[0]


# ─── _handle_request_failed ─────────────────────────────────────────────────


class TestHandleRequestFailed:
    def test_appends_failure_record(self) -> None:
        """Failed request → status=None + failure field present."""
        subj = _make_subject()
        request = SimpleNamespace(
            url="https://x/api",
            method="POST",
            resource_type="xhr",
            failure="net::ERR_CONNECTION_REFUSED",
        )
        subj._handle_request_failed(request)
        record = subj._network_requests[0]
        assert record["status"] is None
        assert record["failure"] == "net::ERR_CONNECTION_REFUSED"
        assert record["url"] == "https://x/api"

    def test_failure_record_does_not_set_status_text(self) -> None:
        """Failed request records have NO 'status_text' key."""
        subj = _make_subject()
        request = SimpleNamespace(url="https://x", method="GET", resource_type="document", failure="DNS")
        subj._handle_request_failed(request)
        assert "status_text" not in subj._network_requests[0]


# ─── _append_network_request: drop counter ─────────────────────────────────


class TestAppendDropCounter:
    def test_no_drop_when_under_limit(self) -> None:
        """deque under maxlen → no drop."""
        subj = _make_subject(maxlen=3)
        subj._append_network_request({"url": "/a"})
        subj._append_network_request({"url": "/b"})
        assert subj._network_requests_dropped == 0

    def test_drop_counter_increments_when_full(self) -> None:
        """Each append at-capacity bumps the drop counter (deque eviction is implicit)."""
        subj = _make_subject(maxlen=2)
        subj._append_network_request({"url": "/a"})
        subj._append_network_request({"url": "/b"})
        subj._append_network_request({"url": "/c"})  # evicts /a, drop +1
        assert subj._network_requests_dropped == 1
        subj._append_network_request({"url": "/d"})  # evicts /b, drop +1
        assert subj._network_requests_dropped == 2

    def test_no_drop_counter_when_maxlen_is_none(self) -> None:
        """If the deque is unbounded (maxlen=None), no drops counted."""
        subj = SessionNetworkMixin.__new__(SessionNetworkMixin)
        subj._network_requests = deque()  # no maxlen
        subj._network_requests_dropped = 0
        for i in range(5):
            subj._append_network_request({"url": f"/x{i}"})
        assert subj._network_requests_dropped == 0


# ─── get_network_requests: filtering + cursor ──────────────────────────────


def _fill(subj: SessionNetworkMixin, items: list[dict[str, Any]]) -> None:
    for item in items:
        subj._append_network_request(item)


class TestGetRequestsBasic:
    def test_no_filters_returns_all(self) -> None:
        """No filters → every retained request is returned."""
        subj = _make_subject()
        _fill(subj, [{"url": "/a"}, {"url": "/b"}])
        result = subj.get_network_requests()
        assert [r["url"] for r in result["requests"]] == ["/a", "/b"]

    def test_return_shape_pins(self) -> None:
        """Return dict has these five keys exactly."""
        subj = _make_subject()
        _fill(subj, [{"url": "/x"}])
        result = subj.get_network_requests()
        assert set(result.keys()) == {"requests", "next_cursor", "total", "total_retained", "dropped"}

    def test_total_and_retained_equal_before_drop(self) -> None:
        """When nothing dropped, total == total_retained == len(deque)."""
        subj = _make_subject()
        _fill(subj, [{"url": f"/u{i}"} for i in range(3)])
        result = subj.get_network_requests()
        assert result["total"] == 3
        assert result["total_retained"] == 3
        assert result["dropped"] == 0


class TestGetRequestsFilters:
    def test_url_filter_filters(self) -> None:
        """URL substring filter."""
        subj = _make_subject()
        _fill(subj, [{"url": "/login"}, {"url": "/dashboard"}, {"url": "/login/redirect"}])
        result = subj.get_network_requests(url_filter="login")
        assert [r["url"] for r in result["requests"]] == ["/login", "/login/redirect"]

    def test_method_filter_uppercases(self) -> None:
        """Method filter matches case-insensitively."""
        subj = _make_subject()
        _fill(subj, [{"url": "/x", "method": "GET"}, {"url": "/y", "method": "POST"}])
        result = subj.get_network_requests(method_filter="post")
        assert [r["url"] for r in result["requests"]] == ["/y"]

    def test_resource_type_filter(self) -> None:
        """Resource type filter is exact-match."""
        subj = _make_subject()
        _fill(
            subj,
            [
                {"url": "/x", "resource_type": "xhr"},
                {"url": "/y", "resource_type": "image"},
                {"url": "/z", "resource_type": "xhr"},
            ],
        )
        result = subj.get_network_requests(resource_type_filter="xhr")
        assert [r["url"] for r in result["requests"]] == ["/x", "/z"]

    def test_filters_AND_combined(self) -> None:
        """Multiple filters combine via AND (all() over predicates)."""
        subj = _make_subject()
        _fill(
            subj,
            [
                {"url": "/login", "method": "POST", "resource_type": "xhr"},
                {"url": "/login", "method": "GET", "resource_type": "xhr"},
                {"url": "/dashboard", "method": "POST", "resource_type": "xhr"},
            ],
        )
        result = subj.get_network_requests(url_filter="login", method_filter="post")
        assert [r["url"] for r in result["requests"]] == ["/login"]


class TestGetRequestsCursor:
    def test_since_starts_at_offset(self) -> None:
        """`since=N` skips the first N retained entries."""
        subj = _make_subject()
        _fill(subj, [{"url": f"/u{i}"} for i in range(5)])
        result = subj.get_network_requests(since=2)
        assert [r["url"] for r in result["requests"]] == ["/u2", "/u3", "/u4"]

    def test_since_is_offset_relative_to_total_with_drops(self) -> None:
        """`since` is interpreted across the full event stream including dropped."""
        subj = _make_subject(maxlen=3)
        _fill(subj, [{"url": f"/u{i}"} for i in range(5)])
        # After 5 appends with maxlen=3, /u0 and /u1 are dropped (drop=2),
        # retained = [/u2, /u3, /u4].
        # since=3 should mean "skip first three of total events" — i.e., the
        # retained_base (2) is subtracted, so start=1 in the retained list.
        result = subj.get_network_requests(since=3)
        assert [r["url"] for r in result["requests"]] == ["/u3", "/u4"]

    def test_since_clamps_at_zero(self) -> None:
        """`since` smaller than retained_base → start=0."""
        subj = _make_subject(maxlen=3)
        _fill(subj, [{"url": f"/u{i}"} for i in range(5)])
        # retained_base = 2. since=1 → start = max(0, 1-2) = 0.
        result = subj.get_network_requests(since=1)
        assert [r["url"] for r in result["requests"]] == ["/u2", "/u3", "/u4"]

    def test_next_cursor_is_total_seen(self) -> None:
        """next_cursor = retained_base + len(retained) — drives client pagination."""
        subj = _make_subject(maxlen=3)
        _fill(subj, [{"url": f"/u{i}"} for i in range(5)])
        result = subj.get_network_requests()
        assert result["next_cursor"] == 5

    def test_dropped_field_reflects_counter(self) -> None:
        """`dropped` field == _network_requests_dropped."""
        subj = _make_subject(maxlen=2)
        _fill(subj, [{"url": f"/u{i}"} for i in range(5)])
        assert subj.get_network_requests()["dropped"] == 3
