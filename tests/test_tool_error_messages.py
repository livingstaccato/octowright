# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A failing tool tells the client why, as it did before mcp 2.1.

mcp 2.1 reduced every exception that is not a ``ToolError`` to ``Error
executing tool <name>``, keeping the cause for the server log only. Octowright's
tools fail with ordinary exceptions whose text is written for the agent to act
on -- the selector Playwright waited for, the SSRF refusal, the unknown launch
option -- so its server restores mcp 2.0's ``Error executing tool <name>:
<cause>`` in one place.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS

from octowright.server._state import _ProfiledMCPServer


def _server() -> _ProfiledMCPServer:
    server = _ProfiledMCPServer(name="octowright-error-probe")

    @server.tool()
    async def boom() -> str:
        raise ValueError("waiting for locator('#pw')")

    @server.tool()
    async def deliberate() -> str:
        raise ToolError("a deliberate refusal")

    @server.tool()
    async def protocol() -> str:
        raise MCPError(INVALID_PARAMS, "bad params")

    return server


async def test_an_ordinary_exception_reaches_the_client_with_its_message() -> None:
    with pytest.raises(ToolError) as raised:
        await _server().call_tool("boom", {})
    assert not isinstance(raised.value, UnexpectedToolError)
    assert str(raised.value) == "Error executing tool boom: waiting for locator('#pw')"
    assert isinstance(raised.value.__cause__, ValueError)


async def test_a_deliberate_tool_error_is_left_as_mcp_reports_it() -> None:
    with pytest.raises(ToolError) as raised:
        await _server().call_tool("deliberate", {})
    assert not isinstance(raised.value, UnexpectedToolError)
    assert str(raised.value) == "Error executing tool deliberate: a deliberate refusal"


async def test_a_protocol_error_still_passes_through() -> None:
    with pytest.raises(MCPError, match="bad params"):
        await _server().call_tool("protocol", {})


class _Log:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, level: str) -> Any:
        def _record(event: str, **fields: Any) -> None:
            self.calls.append((level, event, fields))

        return _record


async def test_a_crashing_tool_is_logged_at_error_with_its_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-raised as ToolError, mcp itself logs the crash at INFO with no
    traceback; octowright logs the original at ERROR so a bug stays visible."""
    from octowright.server import _state

    captured = _Log()
    monkeypatch.setattr(_state, "log", captured)

    with pytest.raises(ToolError):
        await _server().call_tool("boom", {})

    failed = [c for c in captured.calls if c[1] == "octowright.tool.failed"]
    assert len(failed) == 1
    level, _, fields = failed[0]
    assert level in ("error", "exception")
    assert fields["tool"] == "boom"
    assert isinstance(fields["exc_info"], ValueError)
    assert fields["exc_info"].__traceback__ is not None


async def test_a_deliberate_tool_error_is_not_logged_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server import _state

    captured = _Log()
    monkeypatch.setattr(_state, "log", captured)

    with pytest.raises(ToolError):
        await _server().call_tool("deliberate", {})

    assert [c for c in captured.calls if c[1] == "octowright.tool.failed"] == []
