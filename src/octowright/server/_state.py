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
from octowright import terminal as _terminal
from octowright.browser_pool import BrowserPool
from octowright.server._heartbeat import _progress_heartbeat
from octowright.server._idempotency import _idempotent_dispatch
from octowright.server.profiles import active_filter

if TYPE_CHECKING:
    from mcp.types import Icon, ToolAnnotations

    from octowright.terminal.pool import TerminalPool

log = get_logger("octowright.server")

pool = BrowserPool()
scenario_pool = _scenario_pool_mod.ScenarioPool()

# Terminal sessions are an optional feature (the `octowright[terminal]` extra —
# see the terminal-sessions design spec §3.2). `terminal_pool` is None on a core
# install; it is instantiated only when the uterm-backed extra is importable.
# Phase 2's terminal MCP tools register only when this is non-None, so on a core
# install they simply do not appear.
terminal_pool: TerminalPool | None = None
if _terminal.is_available():
    from octowright.terminal.pool import TerminalPool as _TerminalPool

    terminal_pool = _TerminalPool()

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
                # Progress heartbeat wraps OUTERMOST so it also keeps a follower
                # alive while it awaits an in-progress idempotency entry (resend
                # race). Idempotency dedup wraps OUTSIDE advisor tracking so a cache
                # hit skips both re-execution and double-counting; every layer
                # preserves the signature via functools.wraps, so FastMCP Context
                # injection and the input schema still resolve through them.
                return decorator(_progress_heartbeat(_idempotent_dispatch(_track_advisor_usage(fn))))

            return wrap_all

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            # Honour an explicit `@mcp.tool(name="…")` override before falling
            # back to the Python function name. Without this, a tool whose
            # MCP-visible name diverges from `fn.__name__` would silently be
            # filtered out (or in) by the wrong identifier.
            resolved_name = name if name is not None else getattr(fn, "__name__", "")
            if resolved_name not in allowed:
                return fn
            return decorator(_progress_heartbeat(_idempotent_dispatch(_track_advisor_usage(fn))))

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
        "For low-token web browsing, prefer compact discovery before heavy snapshots: use `web_site_links`, "
        "`web_page_outline`, or `web_find_links` before launching a browser when public HTML is enough; "
        "after launch use `browser_page_outline` first for headings/landmarks/links/fields, or "
        "`browser_observe` when you need page outline plus compact console/network/download diagnostics, then "
        "`browser_find_link` or `browser_find_field` to choose a target. Use `browser_read_markdown` for "
        "article/docs text, or `browser_read_markdown(response_mode='summary')` for long pages where you "
        "need a capture_id plus outline before reading line ranges. Use `browser_console_summary` and "
        "`browser_network_summary` for diagnostics, and `browser_downloads_summary` for download checks; "
        "prefer structured next_actions and candidate action payloads for follow-up calls over guessing "
        "raw-tool filters or dumping all rows. "
        "`capture_create(response_mode='summary')` for full-fidelity snapshot/text/evaluate/console/network/"
        "recording payloads that need an inline structural outline without dumping the payload. "
        "pass `response_mode='outline'` on launch/navigate/wait/click/fill/key tools when you need the "
        "post-action page outline without an extra call. "
        "reserve `browser_snapshot`, full console dumps, screenshots, or raw recordings for cases where "
        "the compact tools are insufficient. "
        "Octowright PROACTIVELY pushes MCP notifications for exceptional situations (below); stdio clients "
        "receive them through the follower bridge even in the default detached-daemon deployment. Treat them "
        "as best-effort though — also confirm critical state with `octowright_status()` (health, crash.recent, "
        "pool.lost_sessions) after long operations, after any tool error, and whenever you suspect a crash or "
        "driver loss (a direct HTTP-MCP client that bypasses the follower gets no push). React to a "
        "notification's `hint`: "
        "notifications/octowright/browser_crashed (a page crashed; if recovering=true Octowright is "
        "auto-replacing the page — WAIT for browser_recovered, do NOT relaunch yet); "
        "notifications/octowright/browser_recovered (outcome=recovered → the page is usable again, just "
        "continue; outcome=failed|exhausted → relaunch with browser_launch); "
        "notifications/octowright/driver_died (the shared driver died and these sessions were lost — if "
        "auto-reopen is on, octowright_status().pool.lost_sessions has the old→new instance_id mapping; "
        "otherwise relaunch the browsers you need); "
        "notifications/octowright/session_closed (a session left the pool). "
        "Refused launches surface in-band as tool errors (browser cap reached / available memory below the "
        "floor) with actionable text — don't retry blindly; close browsers or tell the user. "
        "octowright_status() is the pull snapshot for the same signals: health, crash.recent, pool.lost_sessions. "
        "If Octowright tools stop responding or return 'Transport closed' and ONE retry still fails: "
        "STOP immediately. Do NOT run shell commands to restart the daemon (octowright restart, "
        "uv run octowright restart, etc.) — the binary is not on your shell PATH in most agent "
        "environments, and restarting the daemon closes the MCP stdio connection rather than fixing it. "
        "Do NOT run 'which octowright', search the filesystem, or probe /api/health — these waste tokens "
        "and cannot reconnect the MCP client. Do NOT write Playwright scripts or open URLs with shell "
        "commands as substitutes — they are not Octowright sessions. "
        "Instead: tell the user Octowright is disconnected and give them the reconnect step for THEIR "
        "MCP client, then wait for them to confirm before resuming. Reconnect by client (these keep the "
        "conversation unless noted): "
        "Claude Code — /mcp -> select octowright -> Reconnect (if the first try silently fails, choose "
        "Reconnect a second time; the first attempt is a known no-op). "
        "Cursor — Settings -> Tools & MCP -> toggle octowright off then back on. "
        "Cline (VS Code) — MCP Servers panel -> octowright -> Restart Server. "
        "Copilot in VS Code — Command Palette -> 'MCP: List Servers' -> octowright -> Restart. "
        "Windsurf — Cascade plugins (MCP) panel -> Refresh. "
        "Gemini CLI — /mcp disable octowright then /mcp enable octowright (or /mcp refresh). "
        "GitHub Copilot CLI — /mcp reload octowright. "
        "Continue or Zed — re-save the MCP config file (it hot-reloads the server). "
        "Codex CLI, OpenCode, and Amp have NO in-session reconnect: the user must restart the client, "
        "which loses the current conversation. "
        "Universal fallback for any other/unknown client: fully restart the client (recovers the server "
        "but loses the session). If you don't know the client, ask which one they're using. "
        "Wait for the user to confirm reconnection before resuming."
    ),
)
if _allowed_tools is not None:
    log.info("octowright.profile.active", allowed=sorted(_allowed_tools))
