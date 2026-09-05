# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Failed-response body capture (``session/core_network_mixin``).

A failing request used to be recoverable only as its status code, and a 409
from one endpoint can have many distinct causes -- the refusal reason is on
the wire and is usually the whole diagnosis. These pin the scoping that makes
collecting it acceptable: non-2xx only, same-origin only, size-capped.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session.core_network_mixin import (
    NETWORK_BODY_MAX_BYTES_DEFAULT,
    SessionNetworkMixin,
    _same_origin,
    network_body_max_bytes,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Subject(SessionNetworkMixin):
    def __init__(self) -> None:
        self.url = "https://app.test/orders"
        self._bg_tasks: set[Any] = set()


def _response(status: int, url: str = "https://app.test/api", body: bytes = b"{}") -> MagicMock:
    response = MagicMock()
    response.status = status
    response.body = AsyncMock(return_value=body)
    request = MagicMock()
    request.url = url
    response.request = request
    return response


class TestCapConfiguration:
    def test_defaults_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_NETWORK_BODY_MAX_BYTES", raising=False)
        assert network_body_max_bytes() == NETWORK_BODY_MAX_BYTES_DEFAULT

    @pytest.mark.parametrize("token", ["0", "off", "false", "no", "never", "none", "disabled", "OFF"])
    def test_falsey_tokens_disable(self, monkeypatch: pytest.MonkeyPatch, token: str) -> None:
        monkeypatch.setenv("OCTOWRIGHT_NETWORK_BODY_MAX_BYTES", token)
        assert network_body_max_bytes() == 0

    def test_explicit_value_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCTOWRIGHT_NETWORK_BODY_MAX_BYTES", "512")
        assert network_body_max_bytes() == 512

    @pytest.mark.parametrize("bad", ["banana", "-1", "3.5"])
    def test_unparsable_falls_back_to_the_default_not_to_off(self, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
        """This is a diagnostic that is ON by default: a typo must not silently
        remove the one field that explains a failure. Turning it off takes an
        explicit falsey token."""
        monkeypatch.setenv("OCTOWRIGHT_NETWORK_BODY_MAX_BYTES", bad)
        assert network_body_max_bytes() == NETWORK_BODY_MAX_BYTES_DEFAULT


class TestSameOrigin:
    @pytest.mark.parametrize(
        ("candidate", "page", "expected"),
        [
            ("https://app.test/api", "https://app.test/orders", True),
            ("https://app.test:8443/api", "https://app.test/orders", False),  # port differs
            ("http://app.test/api", "https://app.test/orders", False),  # scheme differs
            ("https://cdn.other/api", "https://app.test/orders", False),
            ("https://app.test/api", "", False),  # unknown page origin collects nothing
            ("data:text/plain,x", "https://app.test/orders", False),
        ],
    )
    def test_origin_comparison(self, candidate: str, page: str, expected: bool) -> None:
        assert _same_origin(candidate, page) is expected


class TestCaptureGating:
    @pytest.mark.anyio
    async def test_non_2xx_same_origin_body_is_captured(self) -> None:
        subject = _Subject()
        row: dict[str, Any] = {"url": "https://app.test/api"}
        await subject._read_response_body(
            _response(409, body=b'{"detail": "component_allocation_required"}'), row, 2048, row["url"]
        )
        assert row["body"] == '{"detail": "component_allocation_required"}'
        assert row["body_truncated"] is False

    @pytest.mark.anyio
    async def test_oversized_body_is_capped_and_flagged(self) -> None:
        subject = _Subject()
        row: dict[str, Any] = {"url": "https://app.test/api"}
        await subject._read_response_body(_response(500, body=b"E" * 9000), row, 2048, row["url"])
        assert len(row["body"]) == 2048
        assert row["body_truncated"] is True

    @pytest.mark.anyio
    async def test_undecodable_body_does_not_raise(self) -> None:
        """A binary error payload must degrade, not break the response record."""
        subject = _Subject()
        row: dict[str, Any] = {"url": "https://app.test/api"}
        await subject._read_response_body(_response(500, body=b"\xff\xfe\x00bad"), row, 2048, row["url"])
        assert "body" in row

    @pytest.mark.anyio
    async def test_unreadable_body_leaves_the_row_untouched(self) -> None:
        """Playwright discards a body once the page navigates away. That must
        degrade to today's behaviour, not to a broken row or a raised task."""
        subject = _Subject()
        response = _response(409)
        response.body = AsyncMock(side_effect=RuntimeError("No resource with given identifier"))
        row: dict[str, Any] = {"url": "https://app.test/api"}
        await subject._read_response_body(response, row, 2048, row["url"])
        assert "body" not in row
        assert "body_truncated" not in row

    @pytest.mark.anyio
    @pytest.mark.parametrize("status", [200, 201, 204, 299])
    async def test_successful_responses_schedule_no_read(self, status: int) -> None:
        """Successful bodies are large, numerous and rarely interesting; this
        is what keeps an ordinary page costing nothing."""
        subject = _Subject()
        response = _response(status)
        subject._maybe_capture_body(response, {"url": "https://app.test/api"})
        assert subject._bg_tasks == set()

    @pytest.mark.anyio
    async def test_cross_origin_failure_schedules_no_read(self) -> None:
        """A third party's response body is not the caller's to collect."""
        subject = _Subject()
        response = _response(500, url="https://cdn.other/lib.js")
        subject._maybe_capture_body(response, {"url": "https://cdn.other/lib.js"})
        assert subject._bg_tasks == set()

    @pytest.mark.anyio
    async def test_disabled_cap_schedules_no_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCTOWRIGHT_NETWORK_BODY_MAX_BYTES", "off")
        subject = _Subject()
        subject._maybe_capture_body(_response(409), {"url": "https://app.test/api"})
        assert subject._bg_tasks == set()

    @pytest.mark.anyio
    async def test_failed_same_origin_response_schedules_a_read(self) -> None:
        subject = _Subject()
        subject._maybe_capture_body(_response(409), {"url": "https://app.test/api"})
        assert len(subject._bg_tasks) == 1
        for task in list(subject._bg_tasks):
            await task


class TestNoRunningLoop:
    def test_missing_loop_records_metadata_without_scheduling(self) -> None:
        """Called from a sync harness there is no loop to schedule on. The
        metadata row is already appended by then; only the body is skipped.

        Pinned because three tests in this file once asserted "no task was
        scheduled" from sync bodies, and passed through this branch rather
        than through the scoping they meant to check.
        """
        subject = _Subject()
        subject._maybe_capture_body(_response(409), {"url": "https://app.test/api"})
        assert subject._bg_tasks == set()
