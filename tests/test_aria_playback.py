# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Integration test for ARIA-first macro playback with fallbacks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import macros


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock()
    # Mocking standard click and semantic click_by
    session.click = AsyncMock()
    session.click_by = AsyncMock()
    session.fill = AsyncMock()
    session.fill_by = AsyncMock()
    session.diagnostic_bundle = AsyncMock(return_value={})
    return session


@pytest.mark.anyio
async def test_aria_first_click_success(mock_session: MagicMock) -> None:
    """If role is present, try click_by first. If it passes, don't call click()."""
    action = {
        "action": "click",
        "selector": "#fragile-id",
        "role": "button",
        "role_name": "Login",
    }

    # Execute
    await macros._dispatch_simple(mock_session, action)

    # Verify semantic was called
    mock_session.click_by.assert_awaited_once_with(role="button", role_name="Login")
    # Verify standard fallback was NOT called
    mock_session.click.assert_not_called()


@pytest.mark.anyio
async def test_aria_first_click_fallback(mock_session: MagicMock) -> None:
    """If click_by fails, fallback to standard click(selector)."""
    action = {
        "action": "click",
        "selector": "#fallback-id",
        "role": "button",
        "role_name": "Login",
    }

    # Mock click_by to fail
    mock_session.click_by.side_effect = Exception("Element not found by role")

    # Execute
    await macros._dispatch_simple(mock_session, action)

    # Verify both were called in order
    mock_session.click_by.assert_awaited_once()
    mock_session.click.assert_awaited_once_with(selector="#fallback-id")


@pytest.mark.anyio
async def test_aria_first_fill_success(mock_session: MagicMock) -> None:
    action = {
        "action": "fill",
        "selector": "#email",
        "value": "test@example.com",
        "role": "textbox",
        "role_name": "Email",
    }

    await macros._dispatch_simple(mock_session, action)

    mock_session.fill_by.assert_awaited_once_with(role="textbox", role_name="Email", value="test@example.com")
    mock_session.fill.assert_not_called()


@pytest.mark.anyio
async def test_aria_first_fill_fallback(mock_session: MagicMock) -> None:
    action = {
        "action": "fill",
        "selector": "#legacy-email",
        "value": "test@example.com",
        "role": "textbox",
        "role_name": "Email",
    }

    mock_session.fill_by.side_effect = Exception("Element not found by role")

    await macros._dispatch_simple(mock_session, action)

    mock_session.fill_by.assert_awaited_once()
    mock_session.fill.assert_awaited_once_with(selector="#legacy-email", value="test@example.com")


@pytest.mark.anyio
async def test_aria_first_click_label_only(mock_session: MagicMock) -> None:
    action = {
        "action": "click",
        "selector": "#signup",
        "label": "Sign up",
    }

    await macros._dispatch_simple(mock_session, action)

    mock_session.click_by.assert_awaited_once_with(label="Sign up")
    mock_session.click.assert_not_called()


@pytest.mark.anyio
async def test_aria_first_click_by_action_fallback(mock_session: MagicMock) -> None:
    """If click_by action fails, fall back to click(selector) from the same stored action."""
    action = {
        "action": "click_by",
        "selector": "#legacy-id",
        "role": "button",
        "role_name": "Sign up",
    }

    mock_session.click_by.side_effect = Exception("Element not found by role")

    await macros._dispatch_simple(mock_session, action)

    mock_session.click_by.assert_awaited_once_with(role="button", role_name="Sign up")
    mock_session.click.assert_awaited_once_with(selector="#legacy-id")


@pytest.mark.anyio
async def test_aria_forward_semantic_role_exact(mock_session: MagicMock) -> None:
    action = {
        "action": "click",
        "selector": "#signup",
        "role": "button",
        "role_name": "Submit",
        "role_exact": True,
    }

    await macros._dispatch_simple(mock_session, action)

    mock_session.click_by.assert_awaited_once_with(role="button", role_name="Submit", role_exact=True)
    mock_session.click.assert_not_called()


@pytest.mark.anyio
async def test_aria_first_click_by_action_fallback_without_selector_still_no_fallback(
    mock_session: MagicMock,
) -> None:
    action = {"action": "click_by", "role": "button", "role_name": "No selector"}
    mock_session.click_by.side_effect = Exception("not found")

    with pytest.raises(Exception, match="not found"):
        await macros._dispatch_simple(mock_session, action)

    mock_session.click.assert_not_called()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
