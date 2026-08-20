# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Allocation and registration for a launch whose profile key is held."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import replace
from typing import Any

from octowright._tracing import set_attrs
from octowright.browser_pool.errors import maybe_wrap_playwright_error
from octowright.browser_pool.launch_helpers import (
    _build_viewport_kwargs,
    _open_browser_context,
    base_url_kwargs,
    build_recording_kwargs,
)
from octowright.browser_pool.launch_pipeline import cleanup_failed_launch, post_context_setup
from octowright.browser_pool.options import LaunchOptions, resolve_protected
from octowright.defaults import HEADLESS_DEFAULT
from octowright.recorder import new_log_path


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
    launch_options = replace(launch_options, protected=protected, protected_reason=protected_reason)
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
    launch_kwargs = await pool._build_launch_kwargs(
        disable_gpu=launch_options.disable_gpu,
        tile=launch_options.tile,
        kind=kind,
        headless=headless,
        channel=launch_options.channel,
        executable_path=launch_options.executable_path,
        launch_args=launch_options.launch_args,
    )

    browser: Any | None = None
    context: Any | None = None
    page: Any | None = None
    user_data_dir: str | None = None

    try:
        browser, context, page, user_data_dir = await _open_browser_context(
            browser_type=browser_type,
            kind=kind,
            profile=profile,
            session_user_data_dir=session_user_data_dir,
            headless=headless,
            viewport_kwargs=viewport_kwargs,
            ctx_video_kwargs=ctx_video_kwargs,
            ctx_har_kwargs=ctx_har_kwargs,
            launch_kwargs=launch_kwargs,
            base_url=effective_base_url,
            extra_http_headers=launch_options.extra_http_headers,
            extra_http_headers_urls=launch_options.extra_http_headers_urls,
        )
    except asyncio.CancelledError:
        await cleanup_failed_launch(
            registered=False,
            context=context,
            browser=browser,
            video_dir=video_dir,
            recorder=None,
            pre_register=True,
        )
        raise
    except Exception as exc:
        await cleanup_failed_launch(
            registered=False,
            context=context,
            browser=browser,
            video_dir=video_dir,
            recorder=None,
            pre_register=True,
        )
        wrapped = maybe_wrap_playwright_error(exc, kind=kind)
        if wrapped is exc:
            raise
        raise wrapped from exc

    return await post_context_setup(
        pool,
        launch_options=launch_options,
        instance_id=instance_id,
        t0=t0,
        profile=profile,
        kind=kind,
        label=label,
        target_url=target_url,
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
