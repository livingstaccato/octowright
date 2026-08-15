# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Wrapper-layer tests for the @mcp.tool functions in server/browser/.

Each tool function is a thin proxy from MCP framework to a pool/session
method. We unit-test that the proxy:
  - calls the right session method
  - forwards arguments unchanged
  - returns whatever the session returned

The session methods themselves are covered separately (test_open_url.py,
test_multitab.py, fidelity tests).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server.browser import lifecycle as _lifecycle
from octowright.server.browser import lifecycle_navigate as _nav
from tests._operation_gate_fakes import OperationAwareFake


@pytest.fixture(autouse=True)
def _patch_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the module-level `pool` with a mock for every test.

    ``browser_navigate``/``browser_navigate_back``/``browser_resize``/
    ``browser_viewport_status``/``browser_viewport_sync``/``browser_open_url``
    live in ``lifecycle_navigate`` (Task 10 split, keeps ``lifecycle.py``
    under the LOC ceiling), so both modules' ``pool`` references must point
    at the same fake for a `.get()` call made from either to be observed.
    """
    fake_pool = MagicMock()
    # The browser cap (ON by default) reads pool.active_count() on user-facing
    # launches; a real int keeps the cap check from tripping on the mock.
    fake_pool.active_count.return_value = 0
    monkeypatch.setattr(_lifecycle, "pool", fake_pool)
    monkeypatch.setattr(_nav, "pool", fake_pool)
    return fake_pool


class _FakeSession(OperationAwareFake):
    """Real-gate session fake — ``browser_operation`` awaits ``.operation()``
    as an async context manager, which a bare ``MagicMock`` does not provide."""


def _stub_session(method_name: str, return_value: object) -> _FakeSession:
    """Build a session-like fake whose `method_name` is an AsyncMock."""
    session = _FakeSession()
    setattr(session, method_name, AsyncMock(return_value=return_value))
    return session


# ---------------------------------------------------------------------------
# lifecycle wrappers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_browser_navigate_back_forwards_to_session(_patch_pool: MagicMock) -> None:
    expected = {"ok": True, "url": "https://prev.octowright.com", "title": "Prev"}
    session = _stub_session("navigate_back", expected)
    _patch_pool.get = MagicMock(return_value=session)

    result = await _lifecycle.browser_navigate_back("inst-1")

    _patch_pool.get.assert_called_once_with("inst-1")
    session.navigate_back.assert_awaited_once_with()
    assert result == expected


@pytest.mark.anyio
async def test_browser_navigate_back_outline_mode_returns_page_outline(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = {"ok": True, "url": "https://prev.octowright.com", "title": "Prev"}
    session = _stub_session("navigate_back", expected)
    _patch_pool.get = MagicMock(return_value=session)
    outline = AsyncMock(return_value={"url": "https://prev.octowright.com", "headings": []})
    monkeypatch.setattr(_nav, "browser_page_outline", outline)

    result = await _lifecycle.browser_navigate_back("inst-1", response_mode="outline")

    assert result["outline"]["url"] == "https://prev.octowright.com"
    session.navigate_back.assert_awaited_once_with()
    outline.assert_awaited_once_with("inst-1")


@pytest.mark.anyio
async def test_browser_resize_forwards_dimensions(_patch_pool: MagicMock) -> None:
    expected = {"ok": True, "width": 800, "height": 600}
    session = _stub_session("resize", expected)
    _patch_pool.get = MagicMock(return_value=session)

    result = await _lifecycle.browser_resize("inst-1", 800, 600)

    session.resize.assert_awaited_once_with(800, 600)
    assert result == expected


@pytest.mark.anyio
async def test_browser_viewport_status_forwards_to_session(_patch_pool: MagicMock) -> None:
    expected = {"mode": "fixed", "mismatch": True}
    session = _stub_session("viewport_status", expected)
    _patch_pool.get = MagicMock(return_value=session)

    result = await _lifecycle.browser_viewport_status("inst-1")

    _patch_pool.get.assert_called_once_with("inst-1")
    session.viewport_status.assert_awaited_once_with()
    assert result == expected


@pytest.mark.anyio
async def test_browser_viewport_sync_forwards_to_session(_patch_pool: MagicMock) -> None:
    expected = {"ok": True, "width": 1512, "height": 930}
    session = _stub_session("viewport_sync", expected)
    _patch_pool.get = MagicMock(return_value=session)

    result = await _lifecycle.browser_viewport_sync("inst-1")

    _patch_pool.get.assert_called_once_with("inst-1")
    session.viewport_sync.assert_awaited_once_with()
    assert result == expected


@pytest.mark.anyio
async def test_browser_relaunch_fluid_calls_pool(_patch_pool: MagicMock) -> None:
    expected = {"ok": True, "new_instance_id": "new"}
    _patch_pool.relaunch_fluid = AsyncMock(return_value=expected)

    result = await _lifecycle.browser_relaunch_fluid("inst-1")

    _patch_pool.relaunch_fluid.assert_awaited_once_with("inst-1")
    assert result == expected


@pytest.mark.anyio
async def test_browser_open_url_defaults_to_tab(_patch_pool: MagicMock) -> None:
    expected = {"ok": True, "target": "tab", "page_index": 1, "url": "https://x"}
    session = _stub_session("open_url", expected)
    _patch_pool.get = MagicMock(return_value=session)

    result = await _lifecycle.browser_open_url("inst-1", "https://x")

    session.open_url.assert_awaited_once_with("https://x", target="tab", width=1024, height=768)
    assert result == expected


@pytest.mark.anyio
async def test_browser_open_url_window_target_passes_size(_patch_pool: MagicMock) -> None:
    expected = {"ok": True, "target": "window", "page_index": 2, "url": "https://x"}
    session = _stub_session("open_url", expected)
    _patch_pool.get = MagicMock(return_value=session)

    result = await _lifecycle.browser_open_url("inst-1", "https://x", target="window", width=900, height=700)

    session.open_url.assert_awaited_once_with("https://x", target="window", width=900, height=700)
    assert result == expected


@pytest.mark.anyio
async def test_browser_open_url_outline_mode_returns_page_outline(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = {"ok": True, "target": "tab", "page_index": 1, "url": "https://x"}
    session = _stub_session("open_url", expected)
    _patch_pool.get = MagicMock(return_value=session)
    outline = AsyncMock(return_value={"url": "https://x", "links": []})
    monkeypatch.setattr(_nav, "browser_page_outline", outline)

    result = await _lifecycle.browser_open_url("inst-1", "https://x", response_mode="outline")

    assert result["outline"]["url"] == "https://x"
    session.open_url.assert_awaited_once_with("https://x", target="tab", width=1024, height=768)
    outline.assert_awaited_once_with("inst-1")


@pytest.mark.anyio
async def test_browser_navigate_forwards_to_session(_patch_pool: MagicMock) -> None:
    session = _stub_session("navigate", {"url": "https://x", "title": "X"})
    _patch_pool.get = MagicMock(return_value=session)

    await _lifecycle.browser_navigate("inst-1", "https://x")

    session.navigate.assert_awaited_once_with("https://x")


@pytest.mark.anyio
async def test_browser_close_calls_pool(_patch_pool: MagicMock) -> None:
    _patch_pool.close = AsyncMock(return_value={"closed": True})

    result = await _lifecycle.browser_close("inst-1")

    _patch_pool.close.assert_awaited_once_with("inst-1", force=False)
    assert result == {"closed": True}


@pytest.mark.anyio
async def test_browser_close_protected_error_comes_from_pool(_patch_pool: MagicMock) -> None:
    from octowright.browser_pool.errors import ProtectedBrowserCloseError

    _patch_pool.close = AsyncMock(
        side_effect=ProtectedBrowserCloseError("browser 'inst-1' is protected; pass force=True to close it")
    )

    result = await _lifecycle.browser_close("inst-1")

    _patch_pool.close.assert_awaited_once_with("inst-1", force=False)
    assert "error" in result
    assert "force=True" in result["error"]


@pytest.mark.anyio
async def test_browser_close_reraises_unrelated_value_error(_patch_pool: MagicMock) -> None:
    _patch_pool.close = AsyncMock(side_effect=ValueError("protected word in unrelated lower-level failure"))

    with pytest.raises(ValueError, match="lower-level failure"):
        await _lifecycle.browser_close("inst-1")


@pytest.mark.anyio
async def test_browser_close_protected_force_closes(_patch_pool: MagicMock) -> None:
    _patch_pool.close = AsyncMock(return_value={"closed": True})

    result = await _lifecycle.browser_close("inst-1", force=True)

    _patch_pool.close.assert_awaited_once_with("inst-1", force=True)
    assert result == {"closed": True}


@pytest.mark.anyio
async def test_browser_close_all_calls_pool(_patch_pool: MagicMock) -> None:
    _patch_pool.close_all = AsyncMock(return_value={"closed": ["a", "b"]})

    result = await _lifecycle.browser_close_all()

    _patch_pool.close_all.assert_awaited_once_with(force=False, exclude_labels=None, exclude_profiles=None)
    assert result == {"closed": ["a", "b"]}


@pytest.mark.anyio
async def test_browser_close_all_force_calls_pool_force(_patch_pool: MagicMock) -> None:
    _patch_pool.close_all = AsyncMock(return_value={"closed": ["a", "b"]})

    result = await _lifecycle.browser_close_all(force=True)

    _patch_pool.close_all.assert_awaited_once_with(force=True, exclude_labels=None, exclude_profiles=None)
    assert result == {"closed": ["a", "b"]}


@pytest.mark.anyio
async def test_browser_close_all_skips_protected_and_closes_unprotected_with_force_false(
    _patch_pool: MagicMock,
) -> None:
    _patch_pool.close_all = AsyncMock(return_value={"closed": ["unprotected"], "skipped_protected": ["protected"]})

    result = await _lifecycle.browser_close_all()

    _patch_pool.close_all.assert_awaited_once_with(force=False, exclude_labels=None, exclude_profiles=None)
    assert result["closed"] == ["unprotected"]
    assert result["skipped_protected"] == ["protected"]


@pytest.mark.anyio
async def test_browser_close_all_forwards_exclusions(_patch_pool: MagicMock) -> None:
    _patch_pool.close_all = AsyncMock(return_value={"closed": ["b"]})

    result = await _lifecycle.browser_close_all(exclude_labels=["keep-me"], exclude_profiles=["keep-profile"])

    _patch_pool.close_all.assert_awaited_once_with(
        force=False, exclude_labels=["keep-me"], exclude_profiles=["keep-profile"]
    )
    assert result == {"closed": ["b"]}


@pytest.mark.anyio
async def test_browser_spawn_roster_forwards_specs(_patch_pool: MagicMock) -> None:
    specs = [{"kind": "chromium"}, {"kind": "firefox"}]
    _patch_pool.spawn_roster = AsyncMock(return_value={"launched": [], "errors": []})

    await _lifecycle.browser_spawn_roster(specs)

    _patch_pool.spawn_roster.assert_awaited_once_with(specs)


# ---------------------------------------------------------------------------
# input wrappers (hover / drag / select_option)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_browser_hover_forwards_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server.browser import input as _input

    fake_pool = MagicMock()
    session = _stub_session("hover", None)
    fake_pool.get = MagicMock(return_value=session)
    monkeypatch.setattr(_input, "pool", fake_pool)

    await _input.browser_hover("inst-1", ".selector")

    session.hover.assert_awaited_once_with(".selector")


@pytest.mark.anyio
async def test_browser_drag_forwards_both_selectors(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server.browser import input as _input

    fake_pool = MagicMock()
    session = _stub_session("drag", None)
    fake_pool.get = MagicMock(return_value=session)
    monkeypatch.setattr(_input, "pool", fake_pool)

    await _input.browser_drag("inst-1", ".src", ".dst")

    session.drag.assert_awaited_once_with(".src", ".dst")


@pytest.mark.anyio
async def test_browser_select_option_forwards_value(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server.browser import input as _input

    fake_pool = MagicMock()
    session = _stub_session("select_option", {"ok": True, "selected": ["v"]})
    fake_pool.get = MagicMock(return_value=session)
    monkeypatch.setattr(_input, "pool", fake_pool)

    result = await _input.browser_select_option("inst-1", "select.foo", value="v")

    session.select_option.assert_awaited_once_with("select.foo", value="v", label=None, index=None)
    assert result == {"ok": True, "selected": ["v"]}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
