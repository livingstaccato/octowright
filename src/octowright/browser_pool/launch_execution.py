# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Allocation and registration for a launch whose profile key is held."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from provide.telemetry import get_logger

from octowright._tracing import set_attrs
from octowright.browser_pool import wayland as wayland_mod
from octowright.browser_pool.errors import maybe_wrap_playwright_error
from octowright.browser_pool.launch_helpers import (
    _build_viewport_kwargs,
    _open_browser_context,
    base_url_kwargs,
    build_recording_kwargs,
)
from octowright.browser_pool.launch_pipeline import cleanup_failed_launch, post_context_setup
from octowright.browser_pool.options import LaunchOptions, resolve_protected
from octowright.browser_pool.replacement import recorded_launch_options
from octowright.browser_pool.wayland import WaylandDecision, resolve_wayland_native
from octowright.defaults import HEADLESS_DEFAULT
from octowright.recorder import new_log_path
from octowright.request_errors import InvalidRequestError

log = get_logger(__name__)


async def launch_profile_locked(
    pool: Any,
    launch_options: LaunchOptions,
    sp: Any,
    profile: str | None,
    target_url: str,
) -> dict[str, Any]:
    """Allocate and register a browser while its lifecycle lock is held."""
    kind = launch_options.kind
    headed = launch_options.headed
    label = launch_options.label
    session = launch_options.session
    # Resolve and validate persona-provided base URLs before allocation too,
    # while the persona's lifecycle locks prevent concurrent deletion.
    effective_base_url = base_url_kwargs(profile, launch_options.base_url).get("base_url")

    instance_id = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    session_user_data_dir = await pool._resolve_session_dir(session, launch_options, instance_id, kind)
    set_attrs(sp, instance_id=instance_id, profile=profile, label=label, session=session)
    pw = await pool._ensure_pw()
    browser_type = getattr(pw, kind)
    headless = not headed if headed is not None else HEADLESS_DEFAULT
    protected, protected_reason = resolve_protected(
        launch_options.protected, headed=not headless, ephemeral=launch_options.ephemeral
    )
    # What the session keeps, and what a handoff/relaunch rebuilds from: the
    # request with these launch-time decisions folded in (see replacement).
    launch_options = recorded_launch_options(
        replace(launch_options, protected=protected, protected_reason=protected_reason),
        instance_id=instance_id,
        profile=profile,
        headless=headless,
    )
    log_path = new_log_path(pool._recordings_dir, instance_id, label, kind)

    viewport_kwargs, log_viewport, explicit_size, viewport_info = _build_viewport_kwargs(
        headless, launch_options.viewport_w, launch_options.viewport_h
    )
    ctx_video_kwargs, video_dir, har_path, ctx_har_kwargs = build_recording_kwargs(
        launch_options,
        headless=headless,
        explicit_size=explicit_size,
        log_path=log_path,
        recordings_dir=pool._recordings_dir,
    )
    wayland_decision = resolve_wayland_native(launch_options.wayland_native, kind=kind, headless=headless)
    launch_kwargs = await pool._build_launch_kwargs(
        disable_gpu=launch_options.disable_gpu,
        disable_automation_controlled=launch_options.disable_automation_controlled,
        wayland_native=wayland_decision.effective,
        tile=launch_options.tile,
        kind=kind,
        headless=headless,
        channel=launch_options.channel,
        executable_path=launch_options.executable_path,
        launch_args=launch_options.launch_args,
    )

    async def open_context(kwargs: dict[str, Any]) -> tuple[Any, Any, Any, str | None]:
        return await _open_browser_context(
            browser_type=browser_type,
            kind=kind,
            profile=profile,
            session_user_data_dir=session_user_data_dir,
            headless=headless,
            viewport_kwargs=viewport_kwargs,
            ctx_video_kwargs=ctx_video_kwargs,
            ctx_har_kwargs=ctx_har_kwargs,
            launch_kwargs=kwargs,
            base_url=effective_base_url,
            extra_http_headers=launch_options.extra_http_headers,
            extra_http_headers_urls=launch_options.extra_http_headers_urls,
        )

    async def cleanup() -> None:
        # _open_browser_context closes any browser it launched before raising,
        # so there is no context or browser to close here -- only the video dir.
        await cleanup_failed_launch(
            registered=False,
            context=None,
            browser=None,
            video_dir=video_dir,
            recorder=None,
            pre_register=True,
        )

    try:
        (browser, context, page, user_data_dir), wayland_decision = await open_with_wayland_fallback(
            decision=wayland_decision,
            launch_kwargs=launch_kwargs,
            open_context=open_context,
            cleanup=cleanup,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        wrapped = maybe_wrap_playwright_error(exc, kind=kind)
        if wrapped is exc:
            raise
        raise wrapped from exc

    result = await post_context_setup(
        pool,
        launch_options=launch_options,
        instance_id=instance_id,
        t0=t0,
        profile=profile,
        kind=kind,
        label=label,
        target_url=target_url,
        # The value the context was opened with, so session.base_url reports
        # (and the macro header guard trusts) exactly what Playwright resolves
        # against, without re-reading the persona file.
        base_url=effective_base_url,
        headless=headless,
        log_path=log_path,
        viewport_info=viewport_info,
        log_viewport=log_viewport,
        video_dir=video_dir,
        har_path=har_path,
        browser=browser,
        context=context,
        page=page,
        user_data_dir=user_data_dir,
        session=session,
    )
    if kind == "chromium":
        result["wayland_native"] = wayland_decision.report()
        if wayland_decision.fallback_reason is not None:
            result["wayland_warning"] = (
                "Native Wayland launch failed; this browser runs under XWayland (X11), where "
                f"touchpad pinch-zoom does not reach pages. Chromium reported: {wayland_decision.fallback_reason}"
            )
    return result


async def open_with_wayland_fallback(
    *,
    decision: WaylandDecision,
    launch_kwargs: dict[str, Any],
    open_context: Callable[[dict[str, Any]], Awaitable[Any]],
    cleanup: Callable[[], Awaitable[None]],
) -> tuple[Any, WaylandDecision]:
    """Open the browser; if a native-Wayland launch fails, retry ONCE with X11
    forced (``wayland.x11_retry_kwargs``).

    Only AUTO falls back, and only when the browser blamed Wayland
    (``wayland.mentions_wayland``). Auto chose Wayland on the caller's behalf from a socket
    that exists -- which says nothing about whether a compositor answers on it --
    so its failure must not cost the caller a browser. An explicit request
    (argument, or ``OCTOWRIGHT_WAYLAND_NATIVE=on``) fails loudly instead: quietly
    serving the X11 browser it asked not to get is the "option the caller
    believes took effect" this repository refuses everywhere else.

    The retry is internal to one ``pool.launch``, so engine health and the
    launch metrics see a single outcome: ``ok`` when the X11 browser opened
    (Chromium works on this machine), else the X11 attempt's error. The first
    failure is caught HERE, before ``_launch_with_driver_retry`` sees it, so
    the fallback stays inside this launch. (Its text matches the dead-driver
    markers, but that alone no longer resets the shared driver: the pool
    confirms with ``driver_health.driver_confirmed_dead`` first.)
    """
    try:
        return await open_context(launch_kwargs), decision
    except asyncio.CancelledError:
        await cleanup()
        raise
    except InvalidRequestError:
        # The caller's own request, refused before any engine ran: retrying on
        # X11 would be refused identically.
        await cleanup()
        raise
    except Exception as exc:
        await cleanup()
        # Only a failure the browser itself blamed on Wayland is Wayland's: any
        # other (locked profile, missing library) fails identically on X11, so
        # an auto retry would double the launch time and report an unrelated
        # error as the Wayland reason.
        if not decision.effective or not wayland_mod.mentions_wayland(exc):
            raise
        if decision.source != "auto":
            raise wayland_mod.explicit_failure(decision, exc) from exc
        decision = decision.fell_back(exc)
        log.warning(
            "octowright.launch.wayland_fallback_x11",
            reason=decision.fallback_reason,
            error_type=type(exc).__name__,
        )
    try:
        return await open_context(wayland_mod.x11_retry_kwargs(launch_kwargs)), decision
    except BaseException:
        await cleanup()
        raise
