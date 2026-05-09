# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared singletons for the MCP server: the FastMCP instance, browser/scenario
pools, and the logger. Submodules import from here to register tools against
the same `mcp` and to share the same live state."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from provide.telemetry import get_logger

from octowright import scenarios as _scenarios
from octowright.browser_pool import BrowserPool
from octowright.server.profiles import active_filter

if TYPE_CHECKING:
    from mcp.types import Icon, ToolAnnotations

log = get_logger("octowright.server")

pool = BrowserPool()
scenario_pool = _scenarios.ScenarioPool()


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
            return decorator

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            # Plain `Callable` isn't guaranteed by the type system to carry
            # `__name__`; in practice every @mcp.tool target is a `def` (or
            # async def) which does. Use getattr so ty doesn't object.
            if getattr(fn, "__name__", "") not in allowed:
                return fn
            return decorator(fn)

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
