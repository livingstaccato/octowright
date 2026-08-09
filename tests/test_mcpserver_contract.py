# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pin-tests for the MCP server integration surface octowright depends on.

If the upstream `mcp` package changes shape (rename, signature change,
internal-attribute removal), one of these tests will fail and surface the
drift before users hit it. The tests are intentionally narrow — they pin
the *exact* names/signatures we touch in `server/_state.py` and
`server/registry.py`, not the broader server API.

When the `mcp` package upgrades, run this file first; failures here mean
either the upstream rename matches our integration (update accordingly) or
we should pin the `mcp` package version until we adapt. MCP 2.0 renamed
`mcp.server.fastmcp.FastMCP` to `mcp.server.mcpserver.MCPServer` and dropped
the `request_ctx` contextvar and the client's `get_session_id`; these pins now
track the 2.x surface.
"""

from __future__ import annotations

import inspect

import pytest


def test_mcpserver_module_path_unchanged() -> None:
    """We import `MCPServer` from `mcp.server.mcpserver`."""
    from mcp.server.mcpserver import MCPServer  # noqa: F401


def test_server_middleware_protocol_available() -> None:
    """`_request_context.RequestContextMiddleware` is registered as a
    `ServerMiddleware`; it is how octowright republishes the per-request
    context that MCP 2.0 stopped exposing through a contextvar."""
    from mcp.server.context import ServerMiddleware  # noqa: F401
    from mcp.server.mcpserver import MCPServer

    assert "middleware" in inspect.signature(MCPServer.__init__).parameters


def test_server_request_context_exposes_session_and_meta() -> None:
    """The middleware hands these two fields to the ambient tool wrappers
    (progress heartbeat + idempotent dispatch)."""
    from mcp.server.context import ServerRequestContext

    fields = ServerRequestContext.__dataclass_fields__
    assert "session" in fields
    assert "meta" in fields


def test_streamable_http_client_takes_an_injected_http_client() -> None:
    """The follower bridge builds its own httpx2 client (traceparent injection
    + mcp-session-id capture) and hands it to the transport."""
    from mcp.client.streamable_http import streamable_http_client

    params = inspect.signature(streamable_http_client).parameters
    assert "http_client" in params, (
        "streamable_http_client no longer accepts an injected client — "
        "proxy_runtime builds one via _trace_propagation.build_tracing_http_client."
    )


def test_lowlevel_server_attribute_for_stdio_runner() -> None:
    """`mcp_notifications.run_stdio_with_notifications` reaches through to the
    lowlevel server to capture the write stream."""
    from mcp.server.mcpserver import MCPServer

    low = getattr(MCPServer("contract-pin-test"), "_lowlevel_server", None)
    assert low is not None, "MCPServer no longer exposes `_lowlevel_server`"
    assert callable(getattr(low, "run", None))
    assert callable(getattr(low, "create_initialization_options", None))


def test_tool_annotations_module_path_unchanged() -> None:
    """`ToolAnnotations` is the type our `tool` decorator override forwards."""
    from mcp.types import ToolAnnotations  # noqa: F401


def test_mcpserver_constructor_accepts_name_and_instructions() -> None:
    """`_ProfiledMCPServer("octowright", instructions=...)` is the call site
    in server/_state.py."""
    from mcp.server.mcpserver import MCPServer

    sig = inspect.signature(MCPServer.__init__)
    params = sig.parameters
    # `name` is positional-or-keyword (the server's first arg).
    # We don't pin the *exact* parameter name because some versions call it
    # `name`, some pass via **kwargs to a base class — but `instructions`
    # has been a stable kwarg.
    assert "instructions" in params, (
        "MCPServer no longer accepts `instructions=` kwarg — server/_state.py "
        "passes the LLM-orientation string here. Adapt or pin the mcp version."
    )


def test_mcp_instructions_advertise_compact_browsing_workflow() -> None:
    from octowright.server._state import mcp

    instructions = mcp.instructions

    assert "web_site_links" in instructions
    assert "browser_page_outline" in instructions
    assert "browser_observe" in instructions
    assert "browser_read_markdown" in instructions
    assert "response_mode='outline'" in instructions
    assert "response_mode='summary'" in instructions
    assert "capture_create(response_mode='summary')" in instructions
    assert "browser_snapshot" in instructions
    assert "heavy snapshots" in instructions
    assert "action payload" in instructions
    assert "next_actions" in instructions
    assert "prefer structured next_actions" in instructions
    assert "browser_console_summary" in instructions
    assert "browser_downloads_summary" in instructions


def test_tool_decorator_signature() -> None:
    """`mcp.tool(name=, title=, description=, annotations=)` is the
    decorator surface our @mcp.tool callers expect. _ProfiledMCPServer's
    override forwards exactly these kwargs."""
    from mcp.server.mcpserver import MCPServer

    sig = inspect.signature(MCPServer.tool)
    params = sig.parameters
    for kw in ("name", "description", "annotations"):
        assert kw in params, (
            f"MCPServer.tool() no longer accepts `{kw}=` — server/_state.py's "
            f"_ProfiledMCPServer.tool override forwards this kwarg. Adapt the override."
        )


def test_tool_manager_list_tools_returns_named_objects() -> None:
    """`mcp._tool_manager.list_tools()` is what `registered_tool_names()`
    iterates. We rely on each entry exposing `.name`."""
    from mcp.server.mcpserver import MCPServer

    fresh = MCPServer("contract-pin-test")
    tool_manager = getattr(fresh, "_tool_manager", None)
    assert tool_manager is not None, (
        "MCPServer no longer exposes `_tool_manager` — `registered_tool_names()` "
        "in server/registry.py iterates `mcp._tool_manager.list_tools()`."
    )
    list_tools_fn = getattr(tool_manager, "list_tools", None)
    assert callable(list_tools_fn), "_tool_manager.list_tools is no longer callable."

    @fresh.tool()  # type: ignore[misc]
    def _ping() -> str:
        """Ping."""
        return "pong"

    tools = list_tools_fn()
    assert tools, "expected at least one tool after @mcp.tool registration"
    assert all(hasattr(t, "name") for t in tools), (
        "Tool objects no longer expose `.name` — `registered_tool_names()` depends on iterating these."
    )
    assert "_ping" in {t.name for t in tools}


def test_profile_filter_skips_decoration() -> None:
    """The whole point of `_ProfiledMCPServer`: when `allowed_tools` is set
    and a tool's name is not in it, the decorator returns the function
    unmodified and does NOT register with the underlying server."""
    from octowright.server._state import _ProfiledMCPServer

    fresh = _ProfiledMCPServer("contract-pin-test", allowed_tools={"keep_me"})

    @fresh.tool()  # type: ignore[misc]
    def keep_me() -> str:
        return "yes"

    @fresh.tool()  # type: ignore[misc]
    def skip_me() -> str:
        return "no"

    names = {t.name for t in fresh._tool_manager.list_tools()}
    assert "keep_me" in names
    assert "skip_me" not in names


def test_profile_filter_unset_registers_everything() -> None:
    """`allowed_tools=None` means no filtering (back-compat default)."""
    from octowright.server._state import _ProfiledMCPServer

    fresh = _ProfiledMCPServer("contract-pin-test", allowed_tools=None)

    @fresh.tool()  # type: ignore[misc]
    def alpha() -> str:
        return "a"

    @fresh.tool()  # type: ignore[misc]
    def beta() -> str:
        return "b"

    names = {t.name for t in fresh._tool_manager.list_tools()}
    assert {"alpha", "beta"}.issubset(names)


def test_always_on_tools_pin() -> None:
    """The diagnostic/meta tools are always exempt from the profile filter
    so the LLM can introspect the active profile, find the dashboard URL,
    and detect competing MCP plugins regardless of which `--profile` the
    operator picked. If this list shrinks, also update CLAUDE.md."""
    from octowright.server.profiles import ALWAYS_ON_TOOLS

    assert "octowright_status" in ALWAYS_ON_TOOLS
    assert "octowright_dashboard_url" in ALWAYS_ON_TOOLS
    assert "octowright_check_takeover" in ALWAYS_ON_TOOLS


@pytest.mark.parametrize("profile_name", ["core", "advanced", "macros", "scenarios", "personas"])
def test_named_profiles_exist_and_are_non_empty(profile_name: str) -> None:
    """Profile names CLAUDE.md advertises must exist with at least one tool."""
    from octowright.server.profiles import PROFILES

    assert profile_name in PROFILES, f"{profile_name!r} no longer in PROFILES dict"
    assert PROFILES[profile_name], f"profile {profile_name!r} is empty"
