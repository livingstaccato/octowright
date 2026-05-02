# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Lifecycle tools: launch, suggest_for_url, list, close, close_all, navigate, spawn_roster."""

from __future__ import annotations

from typing import Any, Unpack

from ... import _format as fmt
from ... import resolve as resolve_mod
from ...types import LaunchOptions
from .._state import mcp, pool


@mcp.tool(
    structured_output=False,
    description=(
        "Launch a browser. kind = 'chromium' | 'firefox' | 'webkit'. "
        "BEFORE CALLING THIS for a vague request like 'open google.com' or 'go to discord.com' "
        "where the user did NOT name a persona, FIRST call browser_suggest_for_url(url=...) — "
        "if it reports `ambiguous: true`, ask the user which persona to use instead of guessing. "
        "If it reports `ephemeral_ok: true`, this call with no profile= is fine. "
        "DEFAULT IS HEADED — auto-detected based on OS/environment if headed=None. "
        "Leave headed=None unless you have a specific background-verification reason "
        "(automated health check, scripted parity run, CI). If a human will look at "
        "the window, stay headed. "
        "If profile is given, uses a persistent on-disk user-data-dir so cookies, "
        "localStorage, and IndexedDB survive close/relaunch (recommended for Discord, "
        "Slack, etc.). Profiles are scoped per-kind: (kind, profile) is the identity. "
        "The window title is prefixed with [profile] (or [label] if no profile) so "
        "parallel instances can be told apart in cmd-\\` and the Window menu. "
        "Pass stabilize=True to freeze Date.now, kill CSS animations, and make "
        "requestAnimationFrame synchronous — recommended for reproducible test runs. "
        "Pass trace=True to record a full Playwright trace (screenshots + snapshots + sources) "
        "for post-mortem debugging. Resulting .zip can be viewed with `npx playwright show-trace`. "
        "By default a small translucent corner badge is injected so 10+ parallel browsers "
        "can be visually told apart — same color across engines for the same persona, with "
        "engine emoji distinguishing them. Pass badge=False to disable (recommended for "
        "sites that fingerprint DOM additions, like banks). "
        "badge_position controls the corner (top-left/top-right/bottom-left/bottom-right, "
        "default bottom-right). "
        "Pass tile=True for deterministic tiled window positions (chromium only — "
        "firefox/webkit silently let the OS place the window). Useful when launching "
        "many browsers; rely on the badge for visual differentiation otherwise. "
        "PERSISTENT BY DEFAULT: when you give a label (or profile), the browser uses "
        "an on-disk user-data-dir so cookies/localStorage/IndexedDB survive close. "
        "Pass ephemeral=True for a one-off, no-state-saved launch (good for tests, "
        "incognito-style checks, or when the user explicitly asks 'just this time'). "
        "Pass session=True for a tmpdir profile that lives for the daemon's lifetime "
        "only — state survives close+relaunch within the same daemon, but is wiped on "
        "daemon shutdown. Useful for 'this session only' scopes. Mutually exclusive "
        "with ephemeral=True. Returns instance_id."
    ),
)
async def browser_launch(
    **options: Unpack[LaunchOptions],
) -> dict[str, Any]:
    return await pool.launch(**options)


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
        "ONE-SHOT LAUNCH: Resolves the best persona for a URL and launches it in one call. "
        "Use this for most 'open <url>' tasks to save turns. "
        "Logic: "
        "1. If profile is given, launches directly. "
        "2. If not, calls suggest_for_url internally. "
        "3. If suggest finds a clear high-score persona, uses it. "
        "4. If suggest finds multiple ambiguous options, RETURNS the list and "
        "requires you to pick one via browser_launch. "
        "5. If suggest says ephemeral_ok, launches with no profile. "
        "Returns {instance_id, url, profile_used} on success, or {ambiguous: true, matches: [...]} "
        "if you need to ask the user."
    ),
)
async def browser_quick_launch(
    url: str,
    **options: Unpack[LaunchOptions],
) -> dict[str, Any]:
    profile = options.get("profile")
    kind = options.get("kind", "chromium")

    if profile:
        res = await pool.launch(url=url, **options)
        return {**res, "profile_used": profile}

    # Internal suggest
    suggest = resolve_mod.suggest_for_url(url, kind=kind)
    if suggest.get("ambiguous"):
        return {"ambiguous": True, "matches": suggest["matches"], "url": url}

    # Use recommendation if available
    profile_to_use = None
    if suggest.get("recommendation"):
        profile_to_use = suggest["recommendation"]["persona"]
    elif not suggest.get("ephemeral_ok") and suggest["matches"]:
        # Pick the top match if it's high enough score
        top = suggest["matches"][0]
        if top["score"] >= 80:
            profile_to_use = top["persona"]

    res = await pool.launch(
        url=url,
        profile=profile_to_use,
        **options,
    )
    return {**res, "profile_used": profile_to_use}


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
        "Navigate back in the browser's history (equivalent to clicking the Back button). "
        "Returns {ok, url, title} — ok is False when there is no previous page in history. "
        "Use this after a browser_navigate or link-click to return to the prior page. "
        "Do NOT use for in-app routing where the SPA manages its own history stack."
    ),
)
async def browser_navigate_back(instance_id: str) -> dict[str, Any]:
    return await pool.get(instance_id).navigate_back()


@mcp.tool(
    structured_output=False,
    description=(
        "Resize the browser viewport to the given width x height in CSS pixels. "
        "Use this to test responsive layouts, simulate mobile screen sizes, or ensure "
        "elements are visible at a specific viewport dimension. Does not resize the OS window "
        "— only the page's viewport."
    ),
)
async def browser_resize(instance_id: str, width: int, height: int) -> dict[str, Any]:
    return await pool.get(instance_id).resize(width, height)


@mcp.tool(
    structured_output=False,
    description=(
        "Open a URL in a new tab or new window of an existing instance. "
        "target='tab' (default) opens a new page in the same browser context — "
        "behaves like cmd-T then typing a URL. target='window' opens it in a "
        "separate OS window via window.open(...,'popup',...) — useful when the "
        "user explicitly says 'in a new window'. For 'window', width and height "
        "set the popup window size (defaults 1024x768). Returns {ok, target, "
        "page_index, url}; the new page is appended to the instance's page list "
        "and is the same shape page_switch / page_close use."
    ),
)
async def browser_open_url(
    instance_id: str,
    url: str,
    target: str = "tab",
    width: int = 1024,
    height: int = 768,
) -> dict[str, Any]:
    return await pool.get(instance_id).open_url(url, target=target, width=width, height=height)


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
