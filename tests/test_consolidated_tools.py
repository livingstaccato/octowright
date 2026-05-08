# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for consolidated MCP tools (browser_quick_launch, browser_capture_and_close)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server.browser import inspect as _inspect
from octowright.server.browser import lifecycle as _lifecycle


@pytest.fixture(autouse=True)
def _patch_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replace the module-level `pool` and `resolve_mod` with mocks."""
    fake_pool = MagicMock()
    fake_resolve = MagicMock()

    monkeypatch.setattr(_lifecycle, "pool", fake_pool)
    monkeypatch.setattr(_lifecycle, "resolve_mod", fake_resolve)
    monkeypatch.setattr(_inspect, "pool", fake_pool)

    return {"pool": fake_pool, "resolve": fake_resolve}


def _stub_session(method_name: str, return_value: object) -> MagicMock:
    """Build a session-like mock whose `method_name` is an AsyncMock."""
    session = MagicMock()
    session.log_path = Path("/tmp/test.jsonl")
    session.page = MagicMock()
    session.page.url = "https://example.com"
    session.page.title = AsyncMock(return_value="Example Title")

    # Mock aria_snapshot on page.locator("html")
    html_locator = MagicMock()
    html_locator.aria_snapshot = AsyncMock(return_value="aria tree content")
    session.page.locator = MagicMock(return_value=html_locator)

    if method_name:
        setattr(session, method_name, AsyncMock(return_value=return_value))
    return session


@pytest.mark.anyio
async def test_browser_quick_launch_direct_profile(_patch_state: dict[str, MagicMock]) -> None:
    pool = _patch_state["pool"]
    pool.launch = AsyncMock(return_value={"instance_id": "inst-1"})

    result = await _lifecycle.browser_quick_launch(url="https://x.com", profile="my-persona")

    pool.launch.assert_awaited_once()
    assert result["instance_id"] == "inst-1"
    assert result["profile_used"] == "my-persona"


@pytest.mark.anyio
async def test_browser_quick_launch_with_ambiguous_suggest(_patch_state: dict[str, MagicMock]) -> None:
    resolve = _patch_state["resolve"]
    resolve.suggest_for_url.return_value = {"ambiguous": True, "matches": [{"persona": "A"}, {"persona": "B"}]}

    result = await _lifecycle.browser_quick_launch(url="https://x.com")

    assert result["ambiguous"] is True
    assert len(result["matches"]) == 2


@pytest.mark.anyio
async def test_browser_quick_launch_with_recommendation(_patch_state: dict[str, MagicMock]) -> None:
    pool = _patch_state["pool"]
    resolve = _patch_state["resolve"]
    pool.launch = AsyncMock(return_value={"instance_id": "inst-1"})
    resolve.suggest_for_url.return_value = {
        "ambiguous": False,
        "recommendation": {"persona": "RecPersona"},
        "ephemeral_ok": False,
    }

    result = await _lifecycle.browser_quick_launch(url="https://x.com")

    assert result["instance_id"] == "inst-1"
    assert result["profile_used"] == "RecPersona"

    pool.launch.assert_awaited_once()
    _, kwargs = pool.launch.call_args
    assert kwargs["url"] == "https://x.com"
    assert kwargs["profile"] == "RecPersona"


@pytest.mark.anyio
async def test_browser_quick_launch_missing_url(_patch_state: dict[str, MagicMock]) -> None:
    with pytest.raises(ValueError, match="url is required"):
        await _lifecycle.browser_quick_launch(url="")


@pytest.mark.anyio
async def test_browser_capture_and_close(_patch_state: dict[str, MagicMock]) -> None:
    pool = _patch_state["pool"]
    session = _stub_session("screenshot", Path("/tmp/test.png"))
    pool.get = MagicMock(return_value=session)
    pool.close = AsyncMock(return_value={"closed": True})

    result = await _inspect.browser_capture_and_close("inst-1")

    assert result["title"] == "Example Title"
    assert result["closed"] is True
    assert "aria" in result
    session.screenshot.assert_awaited_once()
    pool.close.assert_awaited_once_with("inst-1")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
