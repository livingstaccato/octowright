# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""TDD tests for unified browser_click and browser_fill.

Written before the implementation — must fail first.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server.browser import input as _input
from tests._operation_gate_fakes import OperationAwareFake


@pytest.fixture(autouse=True)
def _patch_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setattr(_input, "pool", fake)
    return fake


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeSession(OperationAwareFake):
    """Real-gate session fake — browser_click/browser_fill/browser_press_key
    now enter ``browser_operation``, which awaits ``session.operation()`` as
    an async context manager; a bare ``MagicMock`` merely tolerates
    ``async with`` without proving anything."""


def _session(patch: MagicMock) -> _FakeSession:
    s = _FakeSession()
    s.click = AsyncMock(return_value=None)
    s.click_by = AsyncMock(return_value={"ok": True})
    s.fill = AsyncMock(return_value=None)
    s.fill_by = AsyncMock(return_value={"ok": True})
    s.press_key = AsyncMock(return_value=None)
    patch.get.return_value = s
    return s


# ── browser_click — CSS selector path ────────────────────────────────────────


@pytest.mark.anyio
async def test_browser_click_css_selector_calls_session_click(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    out = await _input.browser_click("i", selector="button.submit")
    s.click.assert_awaited_once_with("button.submit")
    assert out["ok"] is True


@pytest.mark.anyio
async def test_browser_click_css_selector_with_brief_returns_brief(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _session(_patch_pool)
    monkeypatch.setattr(_input, "browser_brief", AsyncMock(return_value={"url": "x"}))
    out = await _input.browser_click("i", selector="button", response_mode="brief")
    s.click.assert_awaited_once()
    assert out["brief"]["url"] == "x"


@pytest.mark.anyio
async def test_browser_click_css_selector_with_outline_returns_page_outline(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _session(_patch_pool)
    brief = AsyncMock(return_value={"url": "brief"})
    outline = AsyncMock(return_value={"url": "outline", "headings": []})
    monkeypatch.setattr(_input, "browser_brief", brief)
    monkeypatch.setattr(_input, "browser_page_outline", outline)

    out = await _input.browser_click("i", selector="button", response_mode="outline")

    s.click.assert_awaited_once()
    outline.assert_awaited_once_with("i")
    brief.assert_not_awaited()
    assert out["outline"]["url"] == "outline"


# ── browser_click — ARIA locator path ────────────────────────────────────────


@pytest.mark.anyio
async def test_browser_click_role_calls_session_click_by(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    await _input.browser_click("i", role="button", role_name="Submit")
    s.click_by.assert_awaited_once_with(
        role="button",
        role_name="Submit",
        role_exact=False,
        label=None,
        label_exact=False,
        text=None,
        text_exact=False,
        test_id=None,
        timeout_ms=None,
    )
    s.click.assert_not_awaited()


@pytest.mark.anyio
async def test_browser_click_label_calls_session_click_by(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    await _input.browser_click("i", label="Username")
    s.click_by.assert_awaited_once()
    s.click.assert_not_awaited()


@pytest.mark.anyio
async def test_browser_click_text_calls_session_click_by(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    await _input.browser_click("i", text="Sign in")
    s.click_by.assert_awaited_once()
    s.click.assert_not_awaited()


@pytest.mark.anyio
async def test_browser_click_test_id_calls_session_click_by(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    await _input.browser_click("i", test_id="submit-btn")
    s.click_by.assert_awaited_once()
    s.click.assert_not_awaited()


@pytest.mark.anyio
async def test_browser_click_forwards_timeout_ms_to_click_by(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    await _input.browser_click("i", role="button", timeout_ms=3000)
    _, kwargs = s.click_by.call_args
    assert kwargs["timeout_ms"] == 3000


# ── browser_click — no locator ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_browser_click_no_locator_raises(_patch_pool: MagicMock) -> None:
    _session(_patch_pool)
    with pytest.raises(ValueError, match="selector"):
        await _input.browser_click("i")


# ── browser_fill — CSS selector path ─────────────────────────────────────────


@pytest.mark.anyio
async def test_browser_fill_css_selector_calls_session_fill(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    out = await _input.browser_fill("i", "hello", selector="#email")
    s.fill.assert_awaited_once_with("#email", "hello")
    assert out["ok"] is True


@pytest.mark.anyio
async def test_browser_fill_css_selector_with_outline_returns_page_outline(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _session(_patch_pool)
    outline = AsyncMock(return_value={"url": "outline", "fields": []})
    monkeypatch.setattr(_input, "browser_page_outline", outline)

    out = await _input.browser_fill("i", "hello", selector="#email", response_mode="outline")

    s.fill.assert_awaited_once_with("#email", "hello")
    outline.assert_awaited_once_with("i")
    assert out["outline"]["url"] == "outline"


# ── browser_fill — ARIA locator path ─────────────────────────────────────────


@pytest.mark.anyio
async def test_browser_fill_label_calls_session_fill_by(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    await _input.browser_fill("i", "hello", label="Email")
    s.fill_by.assert_awaited_once_with(
        "hello",
        role=None,
        role_name=None,
        role_exact=False,
        label="Email",
        label_exact=False,
        test_id=None,
        timeout_ms=None,
    )
    s.fill.assert_not_awaited()


@pytest.mark.anyio
async def test_browser_fill_role_calls_session_fill_by(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    await _input.browser_fill("i", "hello", role="textbox")
    s.fill_by.assert_awaited_once()
    s.fill.assert_not_awaited()


@pytest.mark.anyio
async def test_browser_fill_test_id_calls_session_fill_by(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    await _input.browser_fill("i", "hello", test_id="email-input")
    s.fill_by.assert_awaited_once()
    s.fill.assert_not_awaited()


@pytest.mark.anyio
async def test_browser_fill_forwards_timeout_ms_to_fill_by(_patch_pool: MagicMock) -> None:
    s = _session(_patch_pool)
    await _input.browser_fill("i", "hello", label="Email", timeout_ms=2000)
    _, kwargs = s.fill_by.call_args
    assert kwargs["timeout_ms"] == 2000


# ── browser_fill — no locator ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_browser_fill_no_locator_raises(_patch_pool: MagicMock) -> None:
    _session(_patch_pool)
    with pytest.raises(ValueError, match="selector"):
        await _input.browser_fill("i", "hello")


@pytest.mark.anyio
async def test_browser_press_key_with_outline_returns_page_outline(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _session(_patch_pool)
    outline = AsyncMock(return_value={"url": "outline", "headings": []})
    monkeypatch.setattr(_input, "browser_page_outline", outline)

    out = await _input.browser_press_key("i", "Enter", response_mode="outline")

    s.press_key.assert_awaited_once_with("Enter")
    outline.assert_awaited_once_with("i")
    assert out["outline"]["url"] == "outline"
