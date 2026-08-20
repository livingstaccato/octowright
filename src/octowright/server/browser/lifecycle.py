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
    _read_project_config,
    get_default_label,
    project_config_str,
)
from octowright.server._state import mcp, pool
from octowright.server.browser.inspect import browser_page_outline
from octowright.server.browser.lifecycle_navigate import (
    browser_navigate,
    browser_navigate_back,
    browser_open_url,
    browser_resize,
    browser_viewport_status,
    browser_viewport_sync,
)
from octowright.server.browser.lifecycle_summary import browser_list_summary_row

__all__ = [
    "browser_close",
    "browser_close_all",
    "browser_launch",
    "browser_list",
    "browser_navigate",
    "browser_navigate_back",
    "browser_open_url",
    "browser_quick_launch",
    "browser_relaunch_fluid",
    "browser_resize",
    "browser_set_protected",
    "browser_spawn_roster",
    "browser_suggest_for_url",
    "browser_viewport_status",
    "browser_viewport_sync",
]


def _enforce_browser_cap(*, adding: int) -> None:
    """Single-launch shim over the pool-layer cap (`browser_pool.limits`).

    The real gate lives in `roster.spawn_roster` so the scenario path can't
    bypass it; this shim covers the single ad-hoc launch tools, which go through
    `pool.launch` (not the roster). Reads the live module ``pool``.
    """
    _limits.enforce_cap(pool, adding=adding)


def _enforce_memory_floor(*, adding: int) -> None:
    """Shim over the pool-layer memory floor (`browser_pool.limits`)."""
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
            "Retry ephemeral/session, switch profile, or raise OCTOWRIGHT_BROWSER_LAUNCH_TIMEOUT_SECONDS."
        ) from exc


async def _maybe_attach_outline(result: dict[str, Any], response_mode: str | None) -> dict[str, Any]:
    if response_mode == "outline" and isinstance(result.get("instance_id"), str):
        result["outline"] = await browser_page_outline(result["instance_id"])
    return result


@mcp.tool(
    structured_output=False,
    description=(
        "Launch a browser. kind = 'chromium' | 'firefox' | 'webkit'. "
        "BEFORE CALLING THIS for a vague request like 'open google.com' or 'go to discord.com' "
        "where the user did NOT name a persona, FIRST call browser_suggest_for_url(url=...) — if it reports "
        "`ambiguous: true`, ask the user which persona to use instead of guessing. If it reports "
        "`ephemeral_ok: true`, this call with no profile= is fine. DEFAULT IS HEADED — auto-detected "
        "based on OS/environment if headed=None. Leave headed=None unless you have a specific "
        "background-verification reason (automated health check, scripted parity run, CI). If a human "
        "will look at the window, stay headed. "
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
        "protected: leave unset (None) to use the default policy — HEADED browsers are "
        "protected automatically (a reflex close is refused) while headless ones stay closeable. "
        "Pass protected=True to force protection, or protected=False to allow a normal close "
        "(e.g. scripted headed work). OCTOWRIGHT_PROTECT_HEADED=0 disables the headed default; "
        "OCTOWRIGHT_PROTECT_BROWSERS=1 protects everything. Returns instance_id. "
        "If the initial navigation fails (network error, bad URL, DNS failure, etc.) the "
        "browser instance is NOT destroyed — it stays alive and registered. The return dict "
        "includes a 'nav_warning' key with the error string. Call browser_navigate(instance_id, url) "
        "to retry navigation or go to a different URL without re-launching. "
        "Pass response_mode='outline' to include a compact browser_page_outline in the "
        "same call when launch produced an instance_id. "
        "channel picks a real installed browser build instead of Playwright's bundled one "
        "(one of chrome, chrome-beta, chrome-dev, chrome-canary, msedge, msedge-beta, "
        "msedge-dev, msedge-canary) — use for native GPU/DRM/codec parity the bundled build "
        "lacks. executable_path points at a specific browser binary directly, bypassing both "
        "the bundled build and channel. launch_args are extra native CLI flags appended after "
        "octowright's own required Chromium args (new-tab override, tiling, /dev/shm "
        "workaround) — a flag here can override one of those if you deliberately choose to. "
        "executable_path/launch_args are a code-execution primitive (an arbitrary local binary "
        "plus arbitrary argv) and are DISABLED BY DEFAULT — passing either raises ValueError "
        "unless the operator has set OCTOWRIGHT_ALLOW_EXECUTABLE_PATH=1 on the daemon; channel "
        "alone (fixed allowlist) needs no opt-in. "
        "All three are launch-time only: never persisted into the recording, so macro replay, "
        "handoff, and fluid relaunch of this instance do NOT carry them forward."
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
    protected: bool | None = None,
    channel: str | None = None,
    executable_path: str | None = None,
    launch_args: list[str] | None = None,
    extra_http_headers: dict[str, str] | None = None,
    disable_gpu: bool | None = None,
    response_mode: str | None = None,
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
        extra_http_headers=extra_http_headers,
        disable_gpu=disable_gpu,
        channel=channel,
        executable_path=executable_path,
        launch_args=launch_args,
    )
    result = await _pool_launch_with_deadline(**options.to_pool_kwargs())
    publish_dashboard_invalidation_nowait("sessions")
    return await _maybe_attach_outline(result, response_mode)


@mcp.tool(
    structured_output=False,
    description=(
        "Resolve an ambiguous URL to a ranked list of saved persona/profile candidates BEFORE calling "
        "browser_launch. Use this whenever the user says 'open <site>', 'go to <site>', or partially "
        "specifies the engine ('open tradewars.com using firefox') without naming a persona. "
        "Pass `kind` when the user named an engine — that narrows the candidate list. "
        "Returns {url, host, kind_filter, matches, ambiguous, ephemeral_ok, recommendation}: "
        "`ambiguous=true` means several saved personas have this host as their default — ASK THE USER which "
        "one to use, don't guess. `ephemeral_ok=true` means no saved persona owns this host — calling "
        "browser_launch with no profile is fine. "
        "Each match has {persona, kind, score, reasons[], last_used} so you can show the "
        "user a sensible disambiguation prompt."
    ),
)
def browser_suggest_for_url(url: str, kind: str | None = None) -> dict[str, Any]:
    return resolve_mod.suggest_for_url(url, kind=kind)


@mcp.tool(
    structured_output=False,
    description=(
        "ONE-SHOT LAUNCH: Resolves the best persona for a URL and launches it in one call. Use this for most "
        "'open <url>' tasks to save turns. Logic: 1. If profile is given, launches directly. 2. If not, "
        "calls suggest_for_url internally. 3. If suggest finds a clear high-score persona, uses it. "
        "4. If suggest finds multiple ambiguous options, RETURNS the list and requires you to pick one via "
        "browser_launch. 5. If suggest says ephemeral_ok, launches with no profile. "
        "Returns {instance_id, url, profile_used} on success, or {ambiguous: true, matches: [...]} "
        "if you need to ask the user. "
        "If the initial navigation fails (network error, bad URL, DNS failure, etc.) the "
        "browser instance is NOT destroyed — it stays alive and registered. The return dict "
        "includes a 'nav_warning' key with the error string. Call browser_navigate(instance_id, url) "
        "to retry navigation or go to a different URL without re-launching. "
        "Pass response_mode='outline' to include a compact browser_page_outline in the "
        "same call when launch produced an instance_id."
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
    protected: bool | None = None,
    response_mode: str | None = None,
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
        return await _maybe_attach_outline({**res, "profile_used": profile}, response_mode)

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
    return await _maybe_attach_outline({**res, "profile_used": profile_to_use}, response_mode)


@mcp.tool(
    structured_output=False,
    description=(
        "List all live browser instances. Returns {summary, count, browsers}: "
        "`summary` is a one-line human-readable gist (e.g. "
        "'3 browsers: dante/webkit @ discord.com/app · ops/firefox @ monitor'); "
        "`browsers` is the structured per-instance data. Pass response_mode='summary' "
        "for bounded rows with browser_page_outline/browser_close action payloads."
    ),
)
def browser_list(response_mode: str | None = None, limit: int = 20) -> dict[str, Any]:
    sessions = pool.list_sessions()
    if response_mode == "summary":
        capped = max(1, min(int(limit), 100))
        rows = sessions[:capped]
        return {
            "summary": fmt.browser_summary(sessions),
            "count": len(sessions),
            "returned": len(rows),
            "truncated": len(rows) < len(sessions),
            "browsers": [browser_list_summary_row(session) for session in rows],
            "next_actions": [
                {"tool": "browser_list", "args": {"response_mode": "summary", "limit": min(len(sessions), capped + 1)}},
                {"tool": "browser_close_all", "args": {}},
            ],
        }
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
        "is set), you must pass force=True to confirm — skips protected browsers otherwise. "
        "Pass exclude_labels and/or exclude_profiles to spare specific sessions (matched "
        "against each session's label/profile) from the bulk close."
    ),
)
async def browser_close_all(
    force: bool = False,
    exclude_labels: list[str] | None = None,
    exclude_profiles: list[str] | None = None,
) -> dict[str, Any]:
    result = await pool.close_all(
        force=force,
        exclude_labels=exclude_labels,
        exclude_profiles=exclude_profiles,
    )
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
    result = await pool.get(instance_id).set_protected_state(protected)
    publish_dashboard_invalidation_nowait("sessions")
    return result


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
