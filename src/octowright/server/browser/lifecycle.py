# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Lifecycle tools: launch, suggest_for_url, list, close, close_all, navigate, spawn_roster."""

from __future__ import annotations

from typing import Any

from ... import _format as fmt
from ... import resolve as resolve_mod
from .._state import mcp, pool


@mcp.tool(
    structured_output=False,
    description=(
        "Launch a browser. kind = 'chromium' | 'firefox' | 'webkit'. "
        "BEFORE CALLING THIS for a vague request like 'open google.com' or 'go to discord.com' "
        "where the user did NOT name a persona, FIRST call browser_suggest_for_url(url=...) — "
        "if it reports `ambiguous: true`, ask the user which persona to use instead of guessing. "
        "If it reports `ephemeral_ok: true`, this call with no profile= is fine. "
        "DEFAULT IS HEADED — leave headed=True unless you have a specific "
        "background-verification reason (automated health check, scripted parity "
        "run, CI). If a human will look at the window, stay headed. "
        "If profile is given, uses a persistent on-disk user-data-dir so cookies, "
        "localStorage, and IndexedDB survive close/relaunch (recommended for Discord, "
        "Slack, etc.). Profiles are scoped per-kind: (kind, profile) is the identity. "
        "The window title is prefixed with [profile] (or [label] if no profile) so "
        "parallel instances can be told apart in cmd-\\` and the Window menu. "
        "Pass stabilize=True to freeze Date.now, kill CSS animations, and make "
        "requestAnimationFrame synchronous — recommended for reproducible test runs. "
        "Pass trace=True to record a full Playwright trace (screenshots + snapshots + sources) "
        "for post-mortem debugging. Resulting .zip can be viewed with `npx playwright show-trace`. "
        "By default a small colored corner badge is injected so 10+ parallel browsers can "
        "be visually told apart — same color across relaunches. Pass badge=False to disable "
        "(recommended for sites that fingerprint DOM additions, like banks). "
        "Pass tile=True for deterministic tiled window positions (chromium only — "
        "firefox/webkit silently let the OS place the window). Useful when launching "
        "many browsers; rely on the badge for visual differentiation otherwise. "
        "Returns instance_id."
    ),
)
async def browser_launch(
    kind: str = "chromium",
    url: str | None = None,
    headed: bool = True,
    label: str | None = None,
    viewport_w: int | None = None,
    viewport_h: int | None = None,
    profile: str | None = None,
    stabilize: bool = False,
    record_video: bool = False,
    trace: bool = False,
    badge: bool = True,
    tile: bool = False,
) -> dict[str, Any]:
    return await pool.launch(
        kind=kind,
        url=url,
        headed=headed,
        label=label,
        viewport_w=viewport_w,
        viewport_h=viewport_h,
        profile=profile,
        stabilize=stabilize,
        record_video=record_video,
        trace=trace,
        badge=badge,
        tile=tile,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Resolve an ambiguous URL to a ranked list of saved persona/profile candidates "
        "BEFORE calling browser_launch. Use this whenever the user says 'open <site>', "
        "'go to <site>', or partially specifies the engine ('open tradewars.com using firefox') "
        "without naming a persona. "
        "Pass `kind` when the user named an engine — that narrows the candidate list. "
        "Returns {url, host, kind_filter, matches, ambiguous, ephemeral_ok, recommendation}: "
        "`ambiguous=true` means several saved personas have this host as their default — "
        "ASK THE USER which one to use, don't guess. "
        "`ephemeral_ok=true` means no saved persona owns this host — calling browser_launch "
        "with no profile is fine. "
        "Each match has {persona, kind, score, reasons[], last_used} so you can show the "
        "user a sensible disambiguation prompt."
    ),
)
def browser_suggest_for_url(url: str, kind: str | None = None) -> dict[str, Any]:
    return resolve_mod.suggest_for_url(url, kind=kind)


@mcp.tool(
    structured_output=False,
    description=(
        "List all live browser instances. Returns {summary, count, browsers}: "
        "`summary` is a one-line human-readable gist (e.g. "
        "'3 browsers: dante/webkit @ discord.com/app · ops/firefox @ monitor'); "
        "`browsers` is the structured per-instance data."
    ),
)
def browser_list() -> dict[str, Any]:
    sessions = pool.list_sessions()
    return {
        "summary": fmt.browser_summary(sessions),
        "count": len(sessions),
        "browsers": sessions,
    }


@mcp.tool(structured_output=False, description="Close one browser instance by id.")
async def browser_close(instance_id: str) -> dict[str, Any]:
    return await pool.close(instance_id)


@mcp.tool(structured_output=False, description="Close every live browser instance.")
async def browser_close_all() -> dict[str, Any]:
    return await pool.close_all()


@mcp.tool(
    structured_output=False,
    description=(
        "Navigate an instance to a URL. Use this to go to a new page; do NOT use for "
        "in-app routing that the SPA handles via clicks (use browser_click instead). "
        "Equivalent to typing the URL in the address bar and hitting enter."
    ),
)
async def browser_navigate(instance_id: str, url: str) -> dict[str, Any]:
    return await pool.get(instance_id).navigate(url)


@mcp.tool(
    structured_output=False,
    description=(
        "Launch several browsers in parallel from a list of launch specs. Each spec is "
        "a dict accepting any subset of: kind, url, headed, label, profile, viewport_w, "
        "viewport_h, stabilize, record_video. Returns {launched: [...], errors: [...]}."
    ),
)
async def browser_spawn_roster(specs: list[dict[str, Any]]) -> dict[str, Any]:
    return await pool.spawn_roster(specs)
