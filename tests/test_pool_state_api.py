# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.browser_pool import BrowserPool


def test_pool_public_state_api_reads_sessions_without_private_callers() -> None:
    pool = BrowserPool()
    session = SimpleNamespace(
        instance_id="abc123",
        kind="webkit",
        label="demo",
        profile="demo",
        url="https://octowright.com",
        log_path="/tmp/demo.jsonl",
        har_path=None,
        protected=False,
    )
    pool._sessions["abc123"] = session  # type: ignore[assignment]

    assert pool.has_session("abc123") is True
    assert pool.maybe_get("abc123") is session
    assert list(pool.iter_sessions()) == [session]
    assert pool.active_count() == 1
    assert pool.list_sessions() == [
        {
            "instance_id": "abc123",
            "kind": "webkit",
            "label": "demo",
            "profile": "demo",
            "url": "https://octowright.com",
            "log_path": "/tmp/demo.jsonl",
            "har_path": None,
            "protected": False,
        }
    ]


@pytest.mark.asyncio
async def test_concurrent_ensure_pw_initializes_playwright_once(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()
    starts = 0

    class FakePlaywrightFactory:
        async def start(self) -> object:
            nonlocal starts
            starts += 1
            await asyncio.sleep(0)
            return object()

    def fake_async_playwright() -> Any:
        return FakePlaywrightFactory()

    monkeypatch.setattr("octowright.browser_pool.pool.async_playwright", fake_async_playwright)

    instances = await asyncio.gather(*(pool._ensure_pw() for _ in range(5)))

    assert starts == 1
    assert len({id(instance) for instance in instances}) == 1


@pytest.mark.asyncio
async def test_concurrent_close_claims_session_once() -> None:
    pool = BrowserPool()
    close_calls = 0

    class FakeSession:
        instance_id = "abc123"
        kind = "webkit"
        label = None
        profile = None
        log_path = "/tmp/demo.jsonl"
        video_path = None
        trace_path = None
        har_path = None

        async def close(self) -> None:
            nonlocal close_calls
            close_calls += 1
            await asyncio.sleep(0.01)

    pool._sessions["abc123"] = FakeSession()  # type: ignore[assignment]

    results = await asyncio.gather(pool.close("abc123"), pool.close("abc123"), return_exceptions=True)

    closed = [result for result in results if isinstance(result, dict)]
    errors = [result for result in results if isinstance(result, KeyError)]
    assert len(closed) == 1
    assert len(errors) == 1
    assert close_calls == 1
