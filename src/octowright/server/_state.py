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
from octowright.server._idempotency import _idempotent_dispatch
from octowright.server.profiles import active_filter

if TYPE_CHECKING:
    from mcp.types import Icon, ToolAnnotations

log = get_logger("octowright.server")

pool = BrowserPool()
scenario_pool = _scenario_pool_mod.ScenarioPool()

# Records how this process's leader was started, so octowright_status can flag
# the fragile inline-fallback mode — a client that became the in-process leader
# because the detached daemon failed to spawn (killing that client then kills
# every browser). Set by cli.serve at leader startup; stays "unknown" in
# processes that never run the leader (pure followers, or before serve wires it).
_LEADER_MODE: dict[str, str | None] = {"mode": "unknown", "inline_reason": None}


def set_leader_mode(mode: str, *, inline_reason: str | None = None) -> None:
    """Record this process's leader mode. ``mode`` is ``"daemon"`` for a detached
    daemon leader, ``"inline"`` when the leader runs inside this process, or
    ``"unknown"``. ``inline_reason`` explains an inline leader — ``"no_singleton"``
    (deliberate) or ``"daemon_spawn_failed"`` (the fragile fallback)."""
    _LEADER_MODE["mode"] = mode
    _LEADER_MODE["inline_reason"] = inline_reason


def leader_mode_snapshot() -> dict[str, str | None]:
    """Return a copy of the recorded leader-mode state."""
    return dict(_LEADER_MODE)


# Holds the post-upgrade "what's new" notice computed once at leader startup
# (see cli.serve + octowright.upgrade). octowright_status surfaces it so the
# agent can present highlights on the first run after an update. None when the
# version is unchanged or before the leader is wired.
_UPGRADE: dict[str, Any] = {"notice": None}


def set_upgrade_notice(notice: dict[str, Any] | None) -> None:
    """Record the upgrade notice for this leader process (or clear it)."""
    _UPGRADE["notice"] = notice


def upgrade_notice_snapshot() -> dict[str, Any] | None:
    """Return a copy of the recorded upgrade notice, or None."""
    notice = _UPGRADE["notice"]
    return dict(notice) if notice is not None else None


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
                # Idempotency dedup wraps OUTSIDE advisor tracking so a cache hit
                # skips both re-execution and double-counting; both layers preserve
                # the signature via functools.wraps, so FastMCP Context injection
                # and the input schema still resolve through them.
                return decorator(_idempotent_dispatch(_track_advisor_usage(fn)))

            return wrap_all

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            # Honour an explicit `@mcp.tool(name="…")` override before falling
            # back to the Python function name. Without this, a tool whose
            # MCP-visible name diverges from `fn.__name__` would silently be
            # filtered out (or in) by the wrong identifier.
            resolved_name = name if name is not None else getattr(fn, "__name__", "")
            if resolved_name not in allowed:
                return fn
            return decorator(_idempotent_dispatch(_track_advisor_usage(fn)))

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
        "`octowright_status` to see the active profile. "
        "If your Octowright tools stop responding (Transport closed / timeout) or disappear from your "
        "tool list, the server is disconnected — NEVER substitute a shell-opened browser "
        "(open/xdg-open/start) and report it as launched; that browser cannot be driven, inspected, or "
        "recorded. Tell the user Octowright is disconnected and give them reconnect steps for the client "
        "they're using. State confidently only Claude Code: `/mcp` -> select the server -> Reconnect. For "
        "any other client, ask the user which MCP client they're in and have them use its MCP "
        "reconnect/refresh control or restart the client — reconnect UIs vary by client and version, so "
        "don't guess a command you're unsure of. Browser tools won't work until it reconnects."
    ),
)
if _allowed_tools is not None:
    log.info("octowright.profile.active", allowed=sorted(_allowed_tools))
