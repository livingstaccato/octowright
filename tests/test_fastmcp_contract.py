# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pin-tests for the FastMCP integration surface octowright depends on.

If the upstream `mcp` package changes shape (rename, signature change,
internal-attribute removal), one of these tests will fail and surface the
drift before users hit it. The tests are intentionally narrow — they pin
the *exact* names/signatures we touch in `server/_state.py` and
`server/registry.py`, not the broader FastMCP API.

When mcp.server.fastmcp upgrades, run this file first; failures here mean
either the upstream rename matches our integration (update accordingly) or
we should pin the `mcp` package version until we adapt.
"""

from __future__ import annotations

import inspect

import pytest


def test_fastmcp_module_path_unchanged() -> None:
    """We import `FastMCP` from `mcp.server.fastmcp`."""
    from mcp.server.fastmcp import FastMCP  # noqa: F401


def test_tool_annotations_module_path_unchanged() -> None:
    """`ToolAnnotations` lives next to `FastMCP` and is the type our `tool`
    decorator override forwards."""
    from mcp.server.fastmcp import FastMCP  # noqa: F401

    # `_state.py` imports ToolAnnotations from one of these two paths
    # depending on mcp version. Either is fine; both vanishing is not.
    try:
        from mcp.server.fastmcp.tools.base import ToolAnnotations
    except ImportError:
        from mcp.types import ToolAnnotations  # noqa: F401


def test_fastmcp_constructor_accepts_name_and_instructions() -> None:
    """`_ProfiledFastMCP("octowright", instructions=...)` is the call site
    in server/_state.py."""
    from mcp.server.fastmcp import FastMCP

    sig = inspect.signature(FastMCP.__init__)
    params = sig.parameters
    # `name` is positional-or-keyword (FastMCP's first arg).
    # We don't pin the *exact* parameter name because some versions call it
    # `name`, some pass via **kwargs to a base class — but `instructions`
    # has been a stable kwarg.
    assert "instructions" in params, (
        "FastMCP no longer accepts `instructions=` kwarg — server/_state.py "
        "passes the LLM-orientation string here. Adapt or pin the mcp version."
    )


def test_fastmcp_tool_decorator_signature() -> None:
    """`mcp.tool(name=, title=, description=, annotations=)` is the
    decorator surface our @mcp.tool callers expect. _ProfiledFastMCP's
    override forwards exactly these kwargs."""
    from mcp.server.fastmcp import FastMCP

    sig = inspect.signature(FastMCP.tool)
    params = sig.parameters
    for kw in ("name", "description", "annotations"):
        assert kw in params, (
            f"FastMCP.tool() no longer accepts `{kw}=` — server/_state.py's "
            f"_ProfiledFastMCP.tool override forwards this kwarg. Adapt the override."
        )


def test_fastmcp_tool_manager_list_tools_returns_named_objects() -> None:
    """`mcp._tool_manager.list_tools()` is what `registered_tool_names()`
    iterates. We rely on each entry exposing `.name`."""
    from mcp.server.fastmcp import FastMCP

    fresh = FastMCP("contract-pin-test")
    tool_manager = getattr(fresh, "_tool_manager", None)
    assert tool_manager is not None, (
        "FastMCP no longer exposes `_tool_manager` — `registered_tool_names()` "
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
        "FastMCP tool objects no longer expose `.name` — `registered_tool_names()` depends on iterating these."
    )
    assert "_ping" in {t.name for t in tools}


def test_profile_filter_skips_decoration() -> None:
    """The whole point of `_ProfiledFastMCP`: when `allowed_tools` is set
    and a tool's name is not in it, the decorator returns the function
    unmodified and does NOT register with the underlying FastMCP."""
    from octowright.server._state import _ProfiledFastMCP

    fresh = _ProfiledFastMCP("contract-pin-test", allowed_tools={"keep_me"})

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
    from octowright.server._state import _ProfiledFastMCP

    fresh = _ProfiledFastMCP("contract-pin-test", allowed_tools=None)

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
