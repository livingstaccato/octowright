# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.defaults import NETWORK_EVENT_LIMIT
from octowright.session.core import BrowserSession
from octowright.session.core_ops_mixin import SessionOpsMixin


class _Recorder:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def record(self, action: str, **kwargs: Any) -> None:
        self.events.append(f"record:{action}")

    def close(self) -> None:
        self.events.append("recorder:close")


class _Request:
    def __init__(self, index: int) -> None:
        self.url = f"https://example.test/{index}"
        self.method = "GET"
        self.resource_type = "document"


class _Response:
    def __init__(self, index: int) -> None:
        self.request = _Request(index)
        self.status = 200
        self.status_text = "OK"


def _session(events: list[str] | None = None) -> BrowserSession:
    events = events if events is not None else []

    async def _close_context() -> None:
        events.append("context:close")

    context = MagicMock()
    context.close = AsyncMock(side_effect=_close_context)

    return BrowserSession(
        instance_id="i",
        kind="chromium",
        label=None,
        url="https://example.test",
        browser=None,
        context=context,
        page=MagicMock(),
        recorder=_Recorder(events),
        log_path=Path("/tmp/octowright-test.jsonl"),
    )


def test_network_capture_is_bounded_and_reports_dropped_count() -> None:
    session = _session()

    for index in range(NETWORK_EVENT_LIMIT + 2):
        session._handle_response(_Response(index))

    result = session.get_network_requests()

    assert result["dropped"] == 2
    assert result["total_retained"] == NETWORK_EVENT_LIMIT
    assert result["total"] == NETWORK_EVENT_LIMIT
    assert result["next_cursor"] == NETWORK_EVENT_LIMIT + 2
    assert len(result["requests"]) == NETWORK_EVENT_LIMIT
    assert result["requests"][0]["url"] == "https://example.test/2"
    assert result["requests"][-1]["url"] == f"https://example.test/{NETWORK_EVENT_LIMIT + 1}"


def test_network_cursor_advances_after_retention_rollover() -> None:
    session = _session()

    for index in range(NETWORK_EVENT_LIMIT):
        session._handle_response(_Response(index))
    cursor = session.get_network_requests()["next_cursor"]

    for index in range(NETWORK_EVENT_LIMIT, NETWORK_EVENT_LIMIT + 3):
        session._handle_response(_Response(index))
    result = session.get_network_requests(since=cursor)

    assert result["next_cursor"] == NETWORK_EVENT_LIMIT + 3
    assert result["dropped"] == 3
    assert [request["url"] for request in result["requests"]] == [
        f"https://example.test/{NETWORK_EVENT_LIMIT}",
        f"https://example.test/{NETWORK_EVENT_LIMIT + 1}",
        f"https://example.test/{NETWORK_EVENT_LIMIT + 2}",
    ]


@pytest.mark.anyio
async def test_close_waits_for_background_task_before_recorder_close() -> None:
    events: list[str] = []
    session = _session(events)

    async def _background_write() -> None:
        await asyncio.sleep(0.01)
        events.append("background:done")

    session._bg_tasks.add(asyncio.create_task(_background_write()))

    await session.close()

    assert events.index("background:done") < events.index("recorder:close")


@pytest.mark.anyio
async def test_close_drains_background_task_before_context_close_and_recorder_close() -> None:
    events: list[str] = []
    session = _session(events)

    async def _background_write() -> None:
        await asyncio.sleep(0.01)
        events.append("background:context-open")

    session._bg_tasks.add(asyncio.create_task(_background_write()))

    await session.close()

    assert events.index("background:context-open") < events.index("context:close")
    assert events.index("background:context-open") < events.index("recorder:close")


@pytest.mark.anyio
async def test_close_cancels_background_tasks_after_drain_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    session = _session(events)
    monkeypatch.setattr(SessionOpsMixin, "_BG_TASK_DRAIN_TIMEOUT_SECONDS", 0.01, raising=False)

    async def _background_write() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            events.append("background:cancelled")

    task = asyncio.create_task(_background_write())
    session._bg_tasks.add(task)

    try:
        await session.close()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert events.index("background:cancelled") < events.index("recorder:close")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
