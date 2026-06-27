# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Lifecycle tools: launch, suggest_for_url, list, close, close_all, navigate, spawn_roster."""

from __future__ import annotations

import asyncio
from typing import Any

from octowright import _format as fmt
from octowright import resolve as resolve_mod
from octowright.browser_pool import limits as _limits
from octowright.browser_pool.errors import ProtectedBrowserCloseError
from octowright.browser_pool.options import LaunchOptions
from octowright.dashboard_events import publish_dashboard_invalidation_nowait
from octowright.defaults import (
    BROWSER_LAUNCH_TIMEOUT_SECONDS,
    PROTECT_BROWSERS_DEFAULT,
    _read_project_config,
    get_default_label,
    project_config_str,
)
from octowright.server._state import mcp, pool
from octowright.server.browser.inspect import browser_brief


def _enforce_browser_cap(*, adding: int) -> None:
    """Single-launch shim over the pool-layer cap (`browser_pool.limits`).

    The real gate lives in `roster.spawn_roster` so the scenario path can't
    bypass it; this shim covers the single ad-hoc launch tools, which go through
    `pool.launch` (not the roster). Reads the live module ``pool``.
    """
    _limits.enforce_cap(pool, adding=adding)


def _enforce_memory_floor(*, adding: int) -> None:
    """Single-launch shim over the pool-layer memory floor (`browser_pool.limits`)."""
    _limits.enforce_memory(adding=adding)


async def _pool_launch_with_deadline(**kwargs: Any) -> dict[str, Any]:
    _enforce_browser_cap(adding=1)
    _enforce_memory_floor(adding=1)
    timeout = BROWSER_LAUNCH_TIMEOUT_SECONDS
    try:
        return await asyncio.wait_for(pool.launch(**kwargs), timeout=timeout)
    except TimeoutError as exc:
        raise TimeoutError(
            f"browser launch exceeded {timeout:.1f}s before a session was ready; "
            "Octowright cancelled the launch so the MCP client stays connected. "
            "Retry with ephemeral=True or a different profile, or raise "
            "OCTOWRIGHT_BROWSER_LAUNCH_TIMEOUT_SECONDS if the site/browser is expected to start slowly."
        ) from exc


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
        "badge_position controls placement — any of: top-left, top-center, top-right, "
        "left-center, right-center, bottom-left, bottom-center, bottom-right (default bottom-right). "
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
        "with ephemeral=True. "
        "Pass protected=True (or set OCTOWRIGHT_PROTECT_BROWSERS=1) to mark this browser "
        "as user-owned: close-capable tools (browser_close, browser_close_all, "
        "browser_capture_and_close) will refuse to close it unless force=True is passed. "
        "Use this for any browser the user is actively watching. Returns instance_id. "
        "If the initial navigation fails (network error, bad URL, DNS failure, etc.) the "
        "browser instance is NOT destroyed — it stays alive and registered. The return dict "
        "includes a 'nav_warning' key with the error string. Call browser_navigate(instance_id, url) "
        "to retry navigation or go to a different URL without re-launching."
    ),
)
async def browser_launch(
    kind: str = "chromium",
    url: str | None = None,
    headed: bool | None = None,
    label: str | None = None,
    profile: str | None = None,
    viewport_w: int | None = None,
    viewport_h: int | None = None,
    stabilize: bool = False,
    record_video: bool = False,
    trace: bool = False,
    har: bool = False,
    har_path: str | None = None,
    har_mode: str = "minimal",
    har_url_filter: str | None = None,
    har_content: str | None = None,
    badge: bool = True,
    badge_position: str = "bottom-right",
    tile: bool = False,
    ephemeral: bool = False,
    session: bool = False,
    protected: bool = PROTECT_BROWSERS_DEFAULT,
) -> dict[str, Any]:
    # When no label/profile is given and the launch isn't explicitly ephemeral,
    # apply context-aware defaults so the browser has a human name and a
    # persistent profile. Explicit ephemeral=True bypasses this entirely.
    if label is None and profile is None and not ephemeral and not session:
        proj_cfg = _read_project_config()
        label = get_default_label()
        # .octowright/config.yaml persona: wins; then try matching a persona by
        # the project slug; fallback leaves profile as None (auto-promoted from label).
        cfg_persona = project_config_str(proj_cfg, "persona")
        cfg_profile = project_config_str(proj_cfg, "profile")
        if cfg_profile:
            profile = cfg_profile
        elif cfg_persona:
            profile = cfg_persona
        else:
            _slug = label.split("/")[-1] if "/" in label else label
            try:
                from octowright.personas import load_persona

                load_persona(_slug)
                profile = _slug
            except Exception:
                pass

    # Single source of truth: LaunchOptions.to_pool_kwargs() — adding a launch
    # field is a one-line edit in options.py, not four parallel sites.
    options = LaunchOptions(
        kind=kind,
        url=url,
        headed=headed,
        label=label,
        profile=profile,
        viewport_w=viewport_w,
        viewport_h=viewport_h,
        stabilize=stabilize,
        record_video=record_video,
        trace=trace,
        har=har,
        har_path=har_path,
        har_mode=har_mode,
        har_url_filter=har_url_filter,
        har_content=har_content,
        badge=badge,
        badge_position=badge_position,
        tile=tile,
        ephemeral=ephemeral,
        session=session,
        protected=protected,
    )
    result = await _pool_launch_with_deadline(**options.to_pool_kwargs())
    publish_dashboard_invalidation_nowait("sessions")
    return result


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
        "if you need to ask the user. "
        "If the initial navigation fails (network error, bad URL, DNS failure, etc.) the "
        "browser instance is NOT destroyed — it stays alive and registered. The return dict "
        "includes a 'nav_warning' key with the error string. Call browser_navigate(instance_id, url) "
        "to retry navigation or go to a different URL without re-launching."
    ),
)
async def browser_quick_launch(
    url: str,
    kind: str = "chromium",
    headed: bool | None = None,
    label: str | None = None,
    profile: str | None = None,
    viewport_w: int | None = None,
    viewport_h: int | None = None,
    stabilize: bool = False,
    record_video: bool = False,
    trace: bool = False,
    har: bool = False,
    har_path: str | None = None,
    har_mode: str = "minimal",
    har_url_filter: str | None = None,
    har_content: str | None = None,
    badge: bool = True,
    badge_position: str = "bottom-right",
    tile: bool = False,
    ephemeral: bool = False,
    session: bool = False,
    protected: bool = PROTECT_BROWSERS_DEFAULT,
) -> dict[str, Any]:
    if not isinstance(url, str) or not url:
        raise ValueError("url is required")

    if label is None and profile is None and not ephemeral and not session:
        proj_cfg = _read_project_config()
        label = get_default_label()
        cfg_persona = project_config_str(proj_cfg, "persona")
        cfg_profile = project_config_str(proj_cfg, "profile")
        if cfg_profile:
            profile = cfg_profile
        elif cfg_persona:
            profile = cfg_persona
        else:
            _slug = label.split("/")[-1] if "/" in label else label
            try:
                from octowright.personas import load_persona

                load_persona(_slug)
                profile = _slug
            except Exception:
                pass

    def _build_options(*, profile_for_launch: str | None, kind_for_launch: str) -> LaunchOptions:
        # Reuses the LaunchOptions schema so this site never drifts from
        # browser_launch above. The two variant fields (profile/kind) come
        # from either the explicit argument or the resolver's suggestion.
        return LaunchOptions(
            kind=kind_for_launch,
            url=url,
            headed=headed,
            label=label,
            profile=profile_for_launch,
            viewport_w=viewport_w,
            viewport_h=viewport_h,
            stabilize=stabilize,
            record_video=record_video,
            trace=trace,
            har=har,
            har_path=har_path,
            har_mode=har_mode,
            har_url_filter=har_url_filter,
            har_content=har_content,
            badge=badge,
            badge_position=badge_position,
            tile=tile,
            ephemeral=ephemeral,
            session=session,
            protected=protected,
        )

    if profile:
        opts = _build_options(profile_for_launch=profile, kind_for_launch=kind)
        res = await _pool_launch_with_deadline(**opts.to_pool_kwargs())
        publish_dashboard_invalidation_nowait("sessions")
        return {**res, "profile_used": profile}

    # Internal suggest
    suggest = resolve_mod.suggest_for_url(url, kind=kind)
    if suggest.get("ambiguous"):
        return {"ambiguous": True, "matches": suggest["matches"], "url": url}

    profile_to_use = None
    recommendation = suggest.get("recommendation")
    if isinstance(recommendation, dict):
        # Back-compat for older resolver tests/clients; the real resolver now
        # returns a human-readable recommendation string.
        profile_to_use = recommendation.get("persona")
    elif not suggest.get("ephemeral_ok") and suggest["matches"]:
        top = suggest["matches"][0]
        if top["score"] >= 2:
            profile_to_use = top["persona"]
            kind = top["kind"]

    opts = _build_options(profile_for_launch=profile_to_use, kind_for_launch=kind)
    res = await _pool_launch_with_deadline(**opts.to_pool_kwargs())
    publish_dashboard_invalidation_nowait("sessions")
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


@mcp.tool(
    structured_output=False,
    description=(
        "Close one browser instance by id. "
        "If the browser was launched with protected=True (or OCTOWRIGHT_PROTECT_BROWSERS=1 "
        "is set), you must pass force=True to confirm the close — this prevents other "
        "agents from accidentally closing a user-visible browser."
    ),
)
async def browser_close(instance_id: str, force: bool = False) -> dict[str, Any]:
    try:
        result = await pool.close(instance_id, force=force)
    except ProtectedBrowserCloseError as exc:
        return {
            "error": str(exc),
        }
    publish_dashboard_invalidation_nowait("sessions")
    return result


@mcp.tool(
    structured_output=False,
    description=(
        "Close every live browser instance. "
        "If any browser was launched with protected=True (or OCTOWRIGHT_PROTECT_BROWSERS=1 "
        "is set), you must pass force=True to confirm — skips protected browsers otherwise."
    ),
)
async def browser_close_all(force: bool = False) -> dict[str, Any]:
    result = await pool.close_all(force=force)
    publish_dashboard_invalidation_nowait("sessions")
    return result


@mcp.tool(
    structured_output=False,
    description=(
        "Set or clear the protected flag on a live browser session. "
        "protected=True — close-capable tools will refuse to close it without "
        "force=True (use for any browser the user is actively watching). "
        "protected=False — removes the protection so the browser can be closed normally. "
        "Returns {instance_id, protected} confirming the new state."
    ),
)
async def browser_set_protected(instance_id: str, protected: bool) -> dict[str, Any]:
    session = pool.get(instance_id)
    session.protected = protected
    publish_dashboard_invalidation_nowait("sessions")
    return {"instance_id": instance_id, "protected": protected}


@mcp.tool(
    structured_output=False,
    description=(
        "Navigate an instance to a URL. Use this to go to a new page; do NOT use for "
        "in-app routing that the SPA handles via clicks (use browser_click instead). "
        "Equivalent to typing the URL in the address bar and hitting enter. "
        "Pass response_mode='brief' to also return a browser_brief snapshot (url, "
        "title, top elements) in the same call — saves a round trip when you would "
        "otherwise immediately call browser_brief next."
    ),
)
async def browser_navigate(
    instance_id: str,
    url: str,
    response_mode: str | None = None,
) -> dict[str, Any]:
    res: dict[str, Any] = await pool.get(instance_id).navigate(url)
    if response_mode == "brief":
        res["brief"] = await browser_brief(instance_id)
    return res


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
    description="Return fixed/fluid viewport status and measured page/window dimensions.",
)
async def browser_viewport_status(instance_id: str) -> dict[str, Any]:
    return await pool.get(instance_id).viewport_status()


@mcp.tool(
    structured_output=False,
    description="Resize a fixed Playwright viewport once to the current measured browser window size.",
)
async def browser_viewport_sync(instance_id: str) -> dict[str, Any]:
    return await pool.get(instance_id).viewport_sync()


@mcp.tool(
    structured_output=False,
    description="Close and relaunch a session as a headed fluid viewport using no_viewport=True.",
)
async def browser_relaunch_fluid(instance_id: str) -> dict[str, Any]:
    result = await pool.relaunch_fluid(instance_id)
    publish_dashboard_invalidation_nowait("sessions")
    return result


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
        "Launch several browsers in parallel from a list of launch specs. Each spec "
        "is a dict accepting any subset of the LaunchOptions fields used by "
        "browser_launch: kind, url, headed, label, profile, viewport_w, viewport_h, "
        "stabilize, record_video, trace, har, har_path, har_mode, har_url_filter, "
        "har_content, badge, badge_position, tile, ephemeral, session, protected. "
        "Set protected=True in a spec to mark that browser as user-owned — "
        "close-capable tools will refuse to close it without force=True. "
        "Returns {launched: [...], errors: [...]}."
    ),
)
async def browser_spawn_roster(specs: list[dict[str, Any]]) -> dict[str, Any]:
    _enforce_browser_cap(adding=len(specs))
    _enforce_memory_floor(adding=len(specs))
    return await pool.spawn_roster(specs)
