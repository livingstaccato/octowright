# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared singletons for the MCP server: the FastMCP instance, browser/scenario
pools, and the logger. Submodules import from here to register tools against
the same `mcp` and to share the same live state."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from provide.telemetry import get_logger

from octowright import scenarios_pool as _scenario_pool_mod
from octowright.browser_pool import BrowserPool
from octowright.server.profiles import active_filter

if TYPE_CHECKING:
    from mcp.types import Icon, ToolAnnotations

log = get_logger("octowright.server")

pool = BrowserPool()
scenario_pool = _scenario_pool_mod.ScenarioPool()


def _track_advisor_usage(fn: Callable[..., Any]) -> Callable[..., Any]:
    tool_name = getattr(fn, "__name__", "")

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
            _record_advisor_tool_call(tool_name)
            return await fn(*args, **kwargs)

        return async_wrapped

    @functools.wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        _record_advisor_tool_call(tool_name)
        return fn(*args, **kwargs)

    return wrapped


def _record_advisor_tool_call(tool_name: str) -> None:
    try:
        from octowright import advisor as _advisor

        _advisor.record_tool_call(tool_name)
    except Exception as exc:
        log.debug("octowright.advisor.record_tool_call_failed", tool=tool_name, error=str(exc))


class _ProfiledFastMCP(FastMCP):
    """FastMCP subclass that honours OCTOWRIGHT_PROFILE at decoration time.

    When ``allowed_tools`` is ``None`` (no profile active), behaviour is
    identical to the parent class. When it is a set, ``tool()``-decorated
    functions whose ``__name__`` is not in the set are returned unchanged
    (and therefore never registered with the underlying FastMCP).
    """

    _allowed_tools: set[str] | None

    def __init__(self, *args: Any, allowed_tools: set[str] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._allowed_tools = allowed_tools

    def tool(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        decorator = super().tool(
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )
        allowed = self._allowed_tools
        if allowed is None:

            def wrap_all(fn: Callable[..., Any]) -> Callable[..., Any]:
                return decorator(_track_advisor_usage(fn))

            return wrap_all

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            # Honour an explicit `@mcp.tool(name="…")` override before falling
            # back to the Python function name. Without this, a tool whose
            # MCP-visible name diverges from `fn.__name__` would silently be
            # filtered out (or in) by the wrong identifier.
            resolved_name = name if name is not None else getattr(fn, "__name__", "")
            if resolved_name not in allowed:
                return fn
            return decorator(_track_advisor_usage(fn))

        return wrap


_allowed_tools = active_filter()
mcp = _ProfiledFastMCP(
    "octowright",
    allowed_tools=_allowed_tools,
    instructions=(
        "Launch and drive multiple headed Playwright browsers in parallel. "
        "Each browser has an instance_id; pass it to every per-browser tool. "
        "Every action is recorded to a JSONL log that can be exported as a Playwright script. "
        "Use the `profile` arg on browser_launch to persist cookies/localStorage/IndexedDB across runs. "
        "The visible tool surface may be slimmed by OCTOWRIGHT_PROFILE / `octowright serve --profile=...`; "
        "if a tool you expect is missing, the operator picked a narrower capability profile — call "
        "`octowright_status` to see the active profile."
    ),
)
if _allowed_tools is not None:
    log.info("octowright.profile.active", allowed=sorted(_allowed_tools))
