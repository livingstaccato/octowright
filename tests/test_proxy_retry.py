# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Follower-side classification of a failed leader connect.

The text a client sees on a bridge error, and the reconnect pacing behind it,
depends on telling "the leader told this follower to slow down" (429/410 with
Retry-After) apart from a dropped connection. This pins the unwrapping (the
transport hands a 429 up inside an ExceptionGroup), the Retry-After parsing,
and the two sentence shapes those states are reported with.
"""

from __future__ import annotations

import time
from email.utils import formatdate

import httpx2

from octowright import proxy_retry
from octowright.version import VERSION


def _status_exc(status: int, *, retry_after: str | None = None) -> httpx2.HTTPStatusError:
    request = httpx2.Request("POST", "http://127.0.0.1:6286/mcp/")
    headers = [("retry-after", retry_after)] if retry_after is not None else []
    response = httpx2.Response(status, request=request, headers=headers)
    return httpx2.HTTPStatusError(f"status {status}", request=request, response=response)


class TestClassify:
    def test_429_wrapped_in_exception_group_is_throttled_with_retry_after(self) -> None:
        """The observed field shape: the transport's task group wraps the refused
        POST, and the follower's old code showed the whole repr as last_error."""
        exc = ExceptionGroup("unhandled errors in a TaskGroup", [_status_exc(429, retry_after="7")])
        failure = proxy_retry.classify_connect_error(exc)
        assert failure.kind == proxy_retry.KIND_THROTTLED
        assert failure.retry_after == 7.0
        assert failure.status == 429

    def test_410_is_refused_with_retry_after(self) -> None:
        exc = ExceptionGroup("unhandled errors in a TaskGroup", [_status_exc(410, retry_after="10")])
        failure = proxy_retry.classify_connect_error(exc)
        assert failure.kind == proxy_retry.KIND_REFUSED
        assert failure.retry_after == 10.0

    def test_other_status_is_generic_http_error(self) -> None:
        failure = proxy_retry.classify_connect_error(_status_exc(503))
        assert failure.kind == proxy_retry.KIND_HTTP_ERROR
        assert failure.status == 503

    def test_status_found_through_cause_chain(self) -> None:
        outer = RuntimeError("connect failed")
        outer.__cause__ = _status_exc(429)
        assert proxy_retry.classify_connect_error(outer).kind == proxy_retry.KIND_THROTTLED

    def test_timeout_and_plain_error(self) -> None:
        assert proxy_retry.classify_connect_error(TimeoutError("connect deadline")).kind == proxy_retry.KIND_TIMEOUT
        assert proxy_retry.classify_connect_error(OSError("reset")).kind == proxy_retry.KIND_ERROR


class TestRetryAfter:
    def test_http_date(self) -> None:
        parsed = proxy_retry._retry_after_seconds(formatdate(time.time() + 30, usegmt=True))
        assert parsed is not None and 20 <= parsed <= 31

    def test_garbage_is_absent_not_zero(self) -> None:
        assert proxy_retry._retry_after_seconds("soon") is None
        assert proxy_retry._retry_after_seconds(None) is None

    def test_past_date_and_negative_clamp(self) -> None:
        assert proxy_retry._retry_after_seconds(formatdate(time.time() - 60, usegmt=True)) is None
        assert proxy_retry._retry_after_seconds("-5") is None


class TestDescribe:
    def test_throttle_names_the_wait_and_both_versions(self) -> None:
        text = proxy_retry.describe_throttle(
            proxy_retry.ConnectFailure(proxy_retry.KIND_THROTTLED, status=429, retry_after=10.0),
            streak=2,
            retry_in=10.0,
            leader_version="0.26.0",
        )
        assert "leader throttled this follower" in text
        assert "retry in 10.0s" in text
        assert f"follower v{VERSION} vs leader v0.26.0" in text
        assert "persistent" not in text, "a short streak must not read as structural"

    def test_loud_streak_says_mismatch_not_flaky(self) -> None:
        """The whole point of the streak: days of anonymized 429 loops read as 'flaky'."""
        text = proxy_retry.describe_throttle(
            proxy_retry.ConnectFailure(proxy_retry.KIND_THROTTLED, status=429, retry_after=None),
            streak=proxy_retry.THROTTLE_LOUD_STREAK,
            retry_in=5.0,
            leader_version=None,
        )
        assert f"throttled {proxy_retry.THROTTLE_LOUD_STREAK} connects in a row" in text
        assert "persistent follower/leader mismatch" in text
        assert "not a flaky connection" in text
        assert "reconnect this MCP client" in text
        assert "vs leader vunknown" in text

    def test_refused_leads_with_remediation(self) -> None:
        text = proxy_retry.describe_refused(retry_in=10.0, leader_version="0.26.0")
        assert "HTTP 410" in text
        assert "reconnect this MCP client" in text
        assert f"follower v{VERSION} vs leader v0.26.0" in text
        assert "pace-retried in 10.0s" in text
