# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server.browser import input as _input
from tests._operation_gate_fakes import OperationAwareFake


@pytest.fixture(autouse=True)
def _patch_pool_input(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
    fake_pool = MagicMock()
    monkeypatch.setattr(_input, "pool", fake_pool)
    return fake_pool


class _FakeSession(OperationAwareFake):
    """Real-gate session fake — every input.py tool now enters
    ``browser_operation``, which awaits ``session.operation()`` as an async
    context manager; a bare ``MagicMock`` merely tolerates ``async with``
    without proving anything."""


@pytest.mark.anyio
async def test_browser_click_brief_mode(_patch_pool_input: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _FakeSession()
    _patch_pool_input.get.return_value = s
    s.click = AsyncMock(return_value={"ok": True})

    monkeypatch.setattr(_input, "browser_brief", AsyncMock(return_value={"url": "test", "elements": "none"}))

    out = await _input.browser_click("i", "button", response_mode="brief")
    assert out["brief"]["url"] == "test"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "call_args", "session_method", "session_return"),
    [
        ("browser_type", ("#search", "octowright"), "type_text", None),
        (
            "browser_set_input_files",
            ("#file", ["/tmp/a.txt"]),
            "set_input_files",
            {"ok": True, "paths": ["/tmp/a.txt"]},
        ),
        ("browser_hover", ("#menu",), "hover", None),
        ("browser_select_option", ("select",), "select_option", {"ok": True, "values": ["pro"]}),
        ("browser_drag", ("#card", "#lane"), "drag", None),
    ],
)
async def test_mutating_input_tools_outline_mode_return_page_outline(
    _patch_pool_input: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    call_args: tuple[object, ...],
    session_method: str,
    session_return: dict[str, object] | None,
) -> None:
    s = _FakeSession()
    method = AsyncMock(return_value=session_return)
    setattr(s, session_method, method)
    _patch_pool_input.get.return_value = s
    outline = AsyncMock(return_value={"url": "https://octowright.test", "headings": []})
    monkeypatch.setattr(_input, "browser_page_outline", outline)

    tool = getattr(_input, tool_name)
    out = await tool("i", *call_args, response_mode="outline")

    assert out["outline"]["url"] == "https://octowright.test"
    outline.assert_awaited_once_with("i")


def test_mutating_input_tool_descriptions_advertise_outline_mode() -> None:
    descriptions = {tool.name: tool.description for tool in _input.mcp._tool_manager.list_tools()}

    for tool_name in (
        "browser_type",
        "browser_set_input_files",
        "browser_hover",
        "browser_select_option",
        "browser_drag",
    ):
        if tool_name in descriptions:
            assert "response_mode='outline'" in descriptions[tool_name]


@pytest.mark.anyio
async def test_browser_get_text_by_truncates_large_text(_patch_pool_input: MagicMock) -> None:
    s = _FakeSession()
    s.get_text_by = AsyncMock(return_value={"text": "x" * 20})
    _patch_pool_input.get.return_value = s

    out = await _input.browser_get_text_by("i", text="Read more", max_chars=5)

    assert out == {
        "text": "xxxxx",
        "truncated": True,
        "text_size": 20,
        "cap": 5,
        "next_actions": [
            {
                "tool": "browser_get_text_by",
                "args": {"instance_id": "i", "text": "Read more", "full": True},
            }
        ],
    }


@pytest.mark.anyio
async def test_browser_get_text_by_full_mode_preserves_text(_patch_pool_input: MagicMock) -> None:
    s = _FakeSession()
    s.get_text_by = AsyncMock(return_value={"text": "x" * 20})
    _patch_pool_input.get.return_value = s

    out = await _input.browser_get_text_by("i", text="Read more", max_chars=5, full=True)

    assert out == {"text": "x" * 20, "truncated": False, "text_size": 20}


# ─── failure containment at the tool boundary (Task 13) ───────────────────


@pytest.mark.anyio
async def test_admitted_arbitrary_exception_releases_the_gate_without_breaking_it(
    _patch_pool_input: MagicMock,
) -> None:
    """An ordinary Playwright failure (bad selector, detached element, ...)
    surfaces through the EXISTING MCP tool error path -- it must release the
    gate normally (state stays "open"), never mark it broken, and never look
    like driver death."""
    from octowright.browser_pool import driver_health

    s = _FakeSession()
    s.click = AsyncMock(side_effect=RuntimeError("element not found"))
    _patch_pool_input.get.return_value = s

    with pytest.raises(RuntimeError, match="element not found") as excinfo:
        await _input.browser_click("i", "#missing")

    assert driver_health.is_driver_dead_error(excinfo.value) is False
    assert s.operation_snapshot()["state"] == "open"
    assert s.operation_snapshot()["active_operation"] is None

    # The session is fully usable again -- no auto-close, no lingering lease.
    s.click = AsyncMock(return_value=None)
    out = await _input.browser_click("i", "#ok")
    assert out == {"ok": True}


@pytest.mark.anyio
async def test_invariant_error_marks_only_that_session_broken(_patch_pool_input: MagicMock) -> None:
    """A genuine operation-gate invariant violation on session A must not
    reach session B or the MCP tool registry -- only A's gate goes broken."""
    from octowright.session.operation.gate import OperationGateInvariantError

    session_a = _FakeSession()
    session_b = _FakeSession()
    session_a.click = AsyncMock(return_value=None)
    session_b.click = AsyncMock(return_value=None)
    _patch_pool_input.get.side_effect = lambda instance_id: {"A": session_a, "B": session_b}[instance_id]

    # Force a genuine invariant violation the same way the gate's own suite
    # does (tests/session/test_operation_gate.py::test_break_locked_fails_
    # queued_waiters_with_invariant_error) -- there is no public API that
    # reaches this state through ordinary use.
    session_a._test_operation_gate._break_locked("test-induced break")

    with pytest.raises(OperationGateInvariantError):
        await _input.browser_click("A", "#buy")
    assert session_a.operation_snapshot()["state"] == "broken"

    out_b = await _input.browser_click("B", "#buy")
    assert out_b == {"ok": True}
    assert session_b.operation_snapshot()["state"] == "open"

    # Tool registration itself stayed usable throughout.
    tool_names = {tool.name for tool in _input.mcp._tool_manager.list_tools()}
    assert "browser_click" in tool_names


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
