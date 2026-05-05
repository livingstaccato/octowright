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


def test_repair_preview_suggests_structured_semantic_replacement(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    macros_dir = tmp_path / "macros"
    macros_dir.mkdir()
    monkeypatch.setattr(macros, "MACROS_DIR", macros_dir)
    macro_path = macros_dir / "login.json"
    original_action = {
        "action": "fill",
        "selector": "#email",
        "value": "{{email}}",
        "role": "textbox",
        "role_name": "Email Address",
    }
    macro_path.write_text(
        '{"name":"login","actions":['
        '{"action":"fill","selector":"#email","value":"{{email}}","role":"textbox","role_name":"Email Address"},'
        '{"action":"click","selector":"#submit"}'
        "]}",
        encoding="utf-8",
    )

    preview = macros.repair_preview("login")

    assert preview["macro"] == "login"
    assert preview["suggestions"][0]["macro"] == "login"
    assert preview["suggestions"][0]["action_index"] == 0
    assert preview["suggestions"][0]["original_action"] == original_action
    assert preview["suggestions"][0]["source"] == "stored_heuristic"
    assert preview["suggestions"][0]["replacement_action"] == {
        "action": "fill_by",
        "value": "{{email}}",
        "role": "textbox",
        "role_name": "Email Address",
    }
    assert "Fill by" in preview["suggestions"][0]["action_preview"]
    assert "Review selector '#email'" in preview["suggestions"][0]["prompt"]
    assert preview["suggestions"][1]["replacement_action"] is None
    assert "Review selector '#submit'" in preview["suggestions"][1]["prompt"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
