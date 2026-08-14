# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server.browser import lifecycle as _lifecycle
from octowright.server.browser import lifecycle_navigate as _nav
from tests._operation_gate_fakes import OperationAwareFake


class _FakeSession(OperationAwareFake):
    """Real-gate session fake — the navigate/resize/viewport/open-url tools
    now enter ``browser_operation``, which awaits ``session.operation()`` as
    an async context manager; a bare ``MagicMock`` does not provide that."""


@pytest.fixture(autouse=True)
def _patch_pool_lifecycle(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_pool = MagicMock()
    monkeypatch.setattr(_lifecycle, "pool", fake_pool)
    # browser_navigate/etc. live in lifecycle_navigate (Task 10 split, keeps
    # lifecycle.py under the LOC ceiling) with their own `pool` reference.
    monkeypatch.setattr(_nav, "pool", fake_pool)
    return fake_pool


@pytest.mark.anyio
async def test_browser_navigate_default_returns_navigate_result(
    _patch_pool_lifecycle: MagicMock,
) -> None:
    s = _FakeSession()
    _patch_pool_lifecycle.get.return_value = s
    s.navigate = AsyncMock(return_value={"url": "https://octowright.com", "title": "Example"})

    out = await _lifecycle.browser_navigate("i", "https://octowright.com")
    assert out == {"url": "https://octowright.com", "title": "Example"}
    assert "brief" not in out


@pytest.mark.anyio
async def test_browser_navigate_brief_mode_includes_brief(
    _patch_pool_lifecycle: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _FakeSession()
    _patch_pool_lifecycle.get.return_value = s
    s.navigate = AsyncMock(return_value={"url": "https://octowright.com", "title": "Example"})

    monkeypatch.setattr(
        _nav,
        "browser_brief",
        AsyncMock(return_value={"url": "https://octowright.com", "title": "Example", "elements": "..."}),
    )

    out = await _lifecycle.browser_navigate("i", "https://octowright.com", response_mode="brief")
    assert out["url"] == "https://octowright.com"
    assert out["title"] == "Example"
    assert out["brief"]["elements"] == "..."


@pytest.mark.anyio
async def test_browser_navigate_outline_mode_includes_page_outline(
    _patch_pool_lifecycle: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _FakeSession()
    _patch_pool_lifecycle.get.return_value = s
    s.navigate = AsyncMock(return_value={"url": "https://octowright.com", "title": "Example"})
    brief = AsyncMock(return_value={"url": "brief"})
    outline = AsyncMock(return_value={"url": "outline", "headings": []})
    monkeypatch.setattr(_nav, "browser_brief", brief)
    monkeypatch.setattr(_nav, "browser_page_outline", outline)

    out = await _lifecycle.browser_navigate("i", "https://octowright.com", response_mode="outline")

    assert out["url"] == "https://octowright.com"
    assert out["outline"]["url"] == "outline"
    outline.assert_awaited_once_with("i")
    brief.assert_not_awaited()


@pytest.mark.anyio
async def test_browser_navigate_brief_mode_degrades_when_brief_times_out(
    _patch_pool_lifecycle: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _FakeSession()
    _patch_pool_lifecycle.get.return_value = s
    s.navigate = AsyncMock(return_value={"url": "https://octowright.com", "title": "Example"})

    async def slow_brief(instance_id: str) -> dict[str, str]:
        await asyncio.sleep(0.02)
        return {"url": "https://octowright.com", "title": "Example", "elements": "..."}

    monkeypatch.setattr(_nav, "SNAPSHOT_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(_nav, "browser_brief", slow_brief)

    out = await _lifecycle.browser_navigate("i", "https://octowright.com", response_mode="brief")

    assert out["url"] == "https://octowright.com"
    assert out["title"] == "Example"
    assert "brief" not in out
    assert "timed out" in out["brief_warning"]


@pytest.mark.anyio
async def test_browser_set_protected_routes_through_set_protected_state(
    _patch_pool_lifecycle: MagicMock,
) -> None:
    """browser_set_protected must mutate through session.set_protected_state
    (the gate's control_update path), not a bare attribute assignment, so
    Task 7's close-race linearization covers this mutation too."""
    s = MagicMock()
    _patch_pool_lifecycle.get.return_value = s
    s.set_protected_state = AsyncMock(return_value={"instance_id": "i", "protected": True})

    out = await _lifecycle.browser_set_protected("i", True)

    s.set_protected_state.assert_awaited_once_with(True)
    assert out == {"instance_id": "i", "protected": True}


def test_browser_list_summary_mode_bounds_rows_and_adds_actions(_patch_pool_lifecycle: MagicMock) -> None:
    _patch_pool_lifecycle.list_sessions.return_value = [
        {
            "instance_id": "alpha",
            "kind": "chromium",
            "label": "ops",
            "profile": "ops",
            "url": "https://example.com/" + ("a" * 300),
            "title": "Operations Dashboard" + ("!" * 300),
            "protected": True,
        },
        {
            "instance_id": "beta",
            "kind": "firefox",
            "label": "qa",
            "url": "https://example.com/docs",
            "title": "Docs",
            "protected": False,
        },
        {
            "instance_id": "gamma",
            "kind": "webkit",
            "url": "https://example.com/login",
            "title": "Login",
        },
    ]

    out = _lifecycle.browser_list(response_mode="summary", limit=2)

    assert out["count"] == 3
    assert out["returned"] == 2
    assert out["truncated"] is True
    assert out["browsers"][0] == {
        "instance_id": "alpha",
        "kind": "chromium",
        "label": "ops",
        "profile": "ops",
        "url": "https://example.com/" + ("a" * 180),
        "title": "Operations Dashboard" + ("!" * 100),
        "protected": True,
        "actions": [
            {"tool": "browser_page_outline", "args": {"instance_id": "alpha"}},
            {"tool": "browser_close", "args": {"instance_id": "alpha"}},
        ],
    }
    assert out["browsers"][1]["actions"] == [
        {"tool": "browser_page_outline", "args": {"instance_id": "beta"}},
        {"tool": "browser_close", "args": {"instance_id": "beta"}},
    ]
    assert out["next_actions"] == [
        {"tool": "browser_list", "args": {"response_mode": "summary", "limit": 3}},
        {"tool": "browser_close_all", "args": {}},
    ]
    assert "summary" in out


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
