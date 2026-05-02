# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for macro self-healing logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import macros


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock()
    session.snapshot = AsyncMock(
        return_value={"aria": '- textbox "Email Address"\n- button "Submit"', "url": "https://example.com"}
    )
    session.diagnostic_bundle = AsyncMock(return_value={"screenshot": "fail.png"})
    return session


@pytest.mark.anyio
async def test_suggest_fix_finds_match(mock_session: MagicMock) -> None:
    action = {"action": "fill", "selector": "#email", "value": "test@example.com"}

    suggestion = await macros._suggest_fix(mock_session, action)

    assert "I was trying to Fill '#email' with 'test@example.com'" in suggestion
    assert "but '#email' failed" in suggestion
    assert "Current A11y tree:" in suggestion
    assert '- textbox "Email Address"' in suggestion
    assert "Based on the A11y tree, what should I use instead?" in suggestion


@pytest.mark.anyio
async def test_suggest_fix_no_match(mock_session: MagicMock) -> None:
    action = {"action": "click", "selector": "#unknown-button"}

    suggestion = await macros._suggest_fix(mock_session, action)

    assert "I was trying to Click '#unknown-button'" in suggestion
    assert "but '#unknown-button' failed" in suggestion
    assert "Current A11y tree:" in suggestion


@pytest.mark.anyio
async def test_suggest_fix_reflects_dom_change(mock_session: MagicMock) -> None:
    action = {"action": "click", "selector": "#login-btn"}

    # Change the mock snapshot to simulate a DOM change
    mock_session.snapshot.return_value = {"aria": '- button "Login NOW"', "url": "https://example.com"}

    suggestion = await macros._suggest_fix(mock_session, action)

    assert "Click '#login-btn'" in suggestion
    assert "but '#login-btn' failed" in suggestion
    assert '- button "Login NOW"' in suggestion
    assert "Based on the A11y tree, what should I use instead?" in suggestion


@pytest.mark.anyio
async def test_run_macro_includes_healing_on_failure(mock_session: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock load_macro to return a simple macro
    macro_data = {"name": "test-macro", "actions": [{"action": "click", "selector": "#email"}]}
    monkeypatch.setattr(macros, "load_macro", lambda name: macro_data)

    # Mock _dispatch_one to raise an error
    monkeypatch.setattr(macros, "_dispatch_one", AsyncMock(side_effect=RuntimeError("Selector not found")))

    with pytest.raises(RuntimeError) as excinfo:
        await macros.run_macro(mock_session, "test-macro")

    payload = excinfo.value.args[0]
    assert "healing_suggestion" in payload
    assert "Email Address" in payload["healing_suggestion"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
