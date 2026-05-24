# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Launch-pipeline helpers extracted from ``BrowserPool._launch_impl``.

The pipeline is split into three pieces:

- ``cleanup_failed_launch`` — unified cleanup for both the pre-register
  context-open phase and the post-register session-setup phase.
- ``post_context_setup`` — recorder construction, session wiring, ``page.goto``,
  registry insertion. Runs after Playwright has handed us a context/page.
- ``build_launch_result`` — assembles the public dict returned to MCP callers.

Splitting these out keeps ``BrowserPool._launch_impl`` readable, keeps each
function below the xenon cyclomatic-complexity baseline, and keeps
``pool.py`` under the 500-LOC project ceiling.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright.browser_pool._metrics import LAUNCH_DURATION, LAUNCHED
from octowright.browser_pool.cleanup import cleanup_on_launch_failure, cleanup_unregistered_launch
from octowright.browser_pool.errors import maybe_wrap_playwright_error
from octowright.browser_pool.launch_helpers import _record_launch_event, _safe_manifest_record
from octowright.browser_pool.listeners import _wire_close_evictor, _wire_listeners, _wire_user_navigation_logger
from octowright.browser_pool.visuals import wire_init_scripts
from octowright.recorder import Recorder
from octowright.session import BrowserSession

if TYPE_CHECKING:
    from octowright.browser_pool.options import LaunchOptions
    from octowright.browser_pool.pool import BrowserPool

log = get_logger(__name__)


async def cleanup_failed_launch(
    *,
    registered: bool,
    context: Any,
    browser: Any,
    video_dir: Path | None,
    recorder: Recorder | None,
    pre_register: bool,
) -> None:
    """Unified cleanup for both launch-failure paths.

    ``pre_register=True`` covers the context-open phase (no recorder yet),
    which only needs ``cleanup_on_launch_failure``. ``pre_register=False``
    covers the post-context phase, where ``registered`` distinguishes a
    successful registration (no cleanup needed — the session owns the
    resources) from a mid-setup failure (``cleanup_unregistered_launch``).
    """
    if pre_register:
        await cleanup_on_launch_failure(context=context, browser=browser, video_dir=video_dir)
        return
    if not registered:
        await cleanup_unregistered_launch(
            context=context,
            browser=browser,
            video_dir=video_dir,
            recorder=recorder,
        )


def build_launch_result(
    *,
    instance_id: str,
    kind: str,
    label: str | None,
    profile: str | None,
    target_url: str,
    log_path: Path,
    record_video: bool,
    trace: bool,
    har_path: Path | None,
    har_mode: str,
    har_url_filter: str | None,
    har_content: str | None,
    log_viewport: Any,
    video_dir: Path | None,
) -> dict[str, Any]:
    """Assemble the dict returned to MCP callers from ``BrowserPool.launch``.

    The dict shape is the public contract documented in
    ``docs/architecture/MCP-SHARED-CONTRACT.md``.
    """
    result: dict[str, Any] = {
        "instance_id": instance_id,
        "kind": kind,
        "label": label,
        "profile": profile,
        "url": target_url,
        "log_path": str(log_path),
        "record_video": record_video,
        "trace": trace,
        "har": bool(har_path),
        "viewport": log_viewport,
    }
    if video_dir is not None:
        result["video_dir"] = str(video_dir)
    if har_path is not None:
        result["har_path"] = str(har_path)
        result["har_mode"] = har_mode
        if har_url_filter:
            result["har_url_filter"] = har_url_filter
        if har_content:
            result["har_content"] = har_content
    return result


def _build_session_object(
    *,
    instance_id: str,
    kind: str,
    label: str | None,
    target_url: str,
    browser: Any,
    context: Any,
    page: Any,
    recorder: Recorder,
    log_path: Path,
    user_data_dir: str | None,
    profile: str | None,
    launch_options: LaunchOptions,
    har_path: Path | None,
    viewport_info: Any,
) -> BrowserSession:
    """Construct the BrowserSession dataclass plus video tracking.

    Pulled out so ``post_context_setup`` stays under xenon's complexity bar.
    """
    new_session = BrowserSession(
        instance_id=instance_id,
        kind=kind,
        label=label,
        url=target_url,
        browser=browser,
        context=context,
        page=page,
        recorder=recorder,
        log_path=log_path,
        user_data_dir=Path(user_data_dir) if user_data_dir is not None else None,
        profile=profile,
        stabilize=launch_options.stabilize,
        trace=launch_options.trace,
        har_path=har_path,
        viewport_mode=viewport_info.mode.value,
        viewport_width=viewport_info.width,
        viewport_height=viewport_info.height,
        _browser_for_close=(browser if browser is not None else getattr(context, "browser", None)),
    )
    # Wire up video tracking — page.video is only non-None when record_video_dir was set.
    if launch_options.record_video and page.video is not None:
        new_session._video = page.video
    return new_session


async def post_context_setup(
    pool: BrowserPool,
    *,
    launch_options: LaunchOptions,
    instance_id: str,
    t0: float,
    profile: str | None,
    kind: str,
    label: str | None,
    target_url: str,
    headless: bool,
    log_path: Path,
    viewport_info: Any,
    log_viewport: Any,
    video_dir: Path | None,
    har_path: Path | None,
    browser: Any,
    context: Any,
    page: Any,
    user_data_dir: str | None,
    session: bool,
) -> dict[str, Any]:
    """Recorder → BrowserSession → wire listeners → goto → register.

    Owns the entire post-context-open phase and the single cleanup branch
    gated by the local ``registered`` flag.
    """
    recorder: Recorder | None = None
    registered = False
    try:
        recorder = Recorder(log_path)
        _record_launch_event(
            recorder,
            instance_id=instance_id,
            kind=kind,
            label=label,
            profile=profile,
            user_data_dir=user_data_dir,
            target_url=target_url,
            headless=headless,
            log_viewport=log_viewport,
            stabilize=launch_options.stabilize,
            record_video=launch_options.record_video,
            video_dir=video_dir,
            trace=launch_options.trace,
            har_path=har_path,
            har_mode=launch_options.har_mode,
            har_url_filter=launch_options.har_url_filter,
            har_content=launch_options.har_content,
            badge=launch_options.badge,
            badge_position=launch_options.badge_position,
            tile=launch_options.tile,
            ephemeral=launch_options.ephemeral,
            session=session,
        )

        # NOTE: the BrowserSession local was named ``session`` for years, but
        # ``session`` is now the public name of the launch flag (session=True
        # for tmpdir profiles). Use ``new_session`` to avoid shadowing the bool.
        new_session = _build_session_object(
            instance_id=instance_id,
            kind=kind,
            label=label,
            target_url=target_url,
            browser=browser,
            context=context,
            page=page,
            recorder=recorder,
            log_path=log_path,
            user_data_dir=user_data_dir,
            profile=profile,
            launch_options=launch_options,
            har_path=har_path,
            viewport_info=viewport_info,
        )
        new_session.attach_console()
        await pool._expose_viewport_binding(context, new_session)
        # Order matters: the close-evictor and user-nav logger publish handler
        # factories on the session so that subsequent _wire_listeners calls
        # (for popup pages) pick them up automatically. Install them BEFORE
        # the initial _wire_listeners call so the initial page also gets them.
        _wire_close_evictor(pool, new_session)
        _wire_user_navigation_logger(new_session)
        _wire_listeners(new_session, page)
        context.on("page", new_session._register_popup)

        await wire_init_scripts(
            context,
            profile=profile,
            label=label,
            instance_id=instance_id,
            kind=kind,
            badge=launch_options.badge,
            badge_position=launch_options.badge_position,
            stabilize=launch_options.stabilize,
            viewport_mode=new_session.viewport_mode,
            viewport_width=new_session.viewport_width,
            viewport_height=new_session.viewport_height,
        )

        if launch_options.trace:
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        from octowright.session.core_page_mixin import _reject_unsafe_url

        _reject_unsafe_url(target_url)
        await page.goto(target_url)

        new_session._schedule_markdown_capture()

        async with pool._sessions_lock:
            pool._sessions[instance_id] = new_session
        _safe_manifest_record(
            instance_id=instance_id,
            kind=kind,
            label=label,
            profile=profile,
            user_data_dir=user_data_dir,
            log_path=log_path,
        )
        registered = True
        LAUNCHED.add(1, attributes={"kind": kind})
        LAUNCH_DURATION.record(time.perf_counter() - t0, attributes={"kind": kind})
        log.info(
            "octowright.browser.launched",
            instance_id=instance_id,
            kind=kind,
            label=label,
            profile=profile,
            url=target_url,
            headed=not headless,
            log_path=str(log_path),
        )
        return build_launch_result(
            instance_id=instance_id,
            kind=kind,
            label=label,
            profile=profile,
            target_url=target_url,
            log_path=log_path,
            record_video=launch_options.record_video,
            trace=launch_options.trace,
            har_path=har_path,
            har_mode=launch_options.har_mode,
            har_url_filter=launch_options.har_url_filter,
            har_content=launch_options.har_content,
            log_viewport=log_viewport,
            video_dir=video_dir,
        )
    except BaseException as exc:
        await cleanup_failed_launch(
            registered=registered,
            context=context,
            browser=browser,
            video_dir=video_dir,
            recorder=recorder,
            pre_register=False,
        )
        if isinstance(exc, asyncio.CancelledError):
            raise
        wrapped = maybe_wrap_playwright_error(exc, kind=kind)
        if wrapped is exc:
            raise
        raise wrapped from exc
