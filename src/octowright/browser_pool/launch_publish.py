# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pre-publication launch wiring, split out of ``launch_pipeline.py`` to keep
that module under the repository's 550-line LOC ceiling.

Everything here runs BEFORE the constructed ``BrowserSession`` is inserted
into ``pool._sessions`` — nothing can resolve it by ``instance_id`` yet, so
none of this needs an operation lease. ``launch_pipeline.post_context_setup``
calls ``_prepare_session_before_publication`` once, then owns the
publish-through-navigate phase itself under one ``browser_launch_navigation``
lease (see that function's docstring) — that phase is NOT here, because it
can only start once the session exists and is about to become resolvable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright.browser_pool.launch_helpers import _record_launch_event
from octowright.browser_pool.listeners import _wire_close_evictor, _wire_listeners, _wire_user_navigation_logger
from octowright.browser_pool.visuals import wire_init_scripts
from octowright.recorder import Recorder
from octowright.session import BrowserSession
from octowright.session.operation_gate import SessionBusyTimeoutError, SessionClosedError, SessionClosingError

if TYPE_CHECKING:
    from octowright.browser_pool.options import LaunchOptions
    from octowright.browser_pool.pool import BrowserPool

log = get_logger(__name__)

# Exact blank/new-tab URLs that mean "user opened an empty tab" (Cmd+T / Ctrl+T)
# rather than a programmatic popup to a real URL. Firefox uses about:newtab /
# about:home; WebKit and window.open('') land on about:blank.
_BLANK_URLS = frozenset({"", "about:blank", "about:newtab", "about:home"})

# Engine new-tab-page URL prefixes. Chromium's own NTP is normally replaced by
# the new-tab override extension (see newtab_extension.py), but match it
# defensively here in case the extension didn't load (e.g. old headless).
_BLANK_URL_PREFIXES = (
    "chrome://newtab",
    "chrome://new-tab-page",
    "chrome-search://local-ntp",
)

# Task references kept alive to prevent GC mid-flight (satisfies RUF006).
_redirect_tasks: set[asyncio.Task[None]] = set()


def _is_blank_newtab_url(url: str | None) -> bool:
    """True when ``url`` is an engine new-tab/blank page we should redirect."""
    if not url:
        return True
    if url in _BLANK_URLS:
        return True
    return any(url.startswith(prefix) for prefix in _BLANK_URL_PREFIXES)


def _make_new_tab_redirector(new_session: BrowserSession) -> Any:
    """Return a sync page-event handler that redirects blank new tabs to /new-tab.

    Waits for domcontentloaded (up to 800 ms) so the URL is settled before
    checking — more reliable than a fixed sleep. This is the Firefox/WebKit
    path (and a Chromium fallback); Chromium normally never reaches the goto
    because the new-tab override extension already replaced the NTP.

    The opener/load-state/URL/goto sequence runs under one
    ``new_tab_redirect`` lease on ``new_session`` — it queues normally
    behind any other in-flight operation on that session instead of
    touching the page unguarded. A gate rejection (session closing/closed/
    busy past the ordinary timeout) is logged, not silently dropped.
    """

    def _on_new_page(new_page: Any) -> None:
        async def _redirect() -> None:
            from octowright.defaults import get_default_url

            try:
                async with new_session.operation("new_tab_redirect"):
                    # Only redirect user-opened tabs (Cmd+T), never
                    # programmatic popups. A window.open(...) popup has an
                    # opener page; a fresh Cmd+T tab does not. Skipping
                    # opened popups leaves app-controlled windows alone.
                    try:
                        opener = await new_page.opener()
                    except Exception:
                        opener = None
                    if opener is not None:
                        return
                    try:
                        await new_page.wait_for_load_state("domcontentloaded", timeout=800)
                    except Exception:
                        pass
                    try:
                        if _is_blank_newtab_url(new_page.url):
                            await new_page.goto(get_default_url())
                    except Exception:
                        pass
            except (SessionClosingError, SessionClosedError, SessionBusyTimeoutError) as exc:
                log.info(
                    "octowright.launch.new_tab_redirect_gate_rejected",
                    instance_id=new_session.instance_id,
                    error=repr(exc),
                )

        task = asyncio.create_task(_redirect())
        _redirect_tasks.add(task)
        task.add_done_callback(_redirect_tasks.discard)

    return _on_new_page


def _build_session_object(
    *,
    pool: BrowserPool,
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
    operation_queue_timeout_seconds: float,
) -> BrowserSession:
    """Construct the BrowserSession dataclass plus video tracking.

    Pulled out so ``_prepare_session_before_publication`` stays under
    xenon's complexity bar.
    """
    # protected must be resolved to a concrete bool before this point — the
    # only caller (_prepare_session_before_publication) is only ever invoked
    # from BrowserPool._launch_impl via post_context_setup, which calls
    # resolve_protected() and rebinds launch_options via dataclasses.replace()
    # before handing off here. See pool.py's resolve_protected call.
    assert launch_options.protected is not None, (  # nosec B101  # narrow for type-checker
        "protected must be resolved to a concrete bool before this point — see pool.py's resolve_protected call"
    )
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
        protected=launch_options.protected,
        protected_reason=launch_options.protected_reason,
        trace=launch_options.trace,
        har_path=har_path,
        viewport_mode=viewport_info.mode.value,
        viewport_width=viewport_info.width,
        viewport_height=viewport_info.height,
        _browser_for_close=(browser if browser is not None else getattr(context, "browser", None)),
        operation_queue_timeout_seconds=operation_queue_timeout_seconds,
    )
    # Wire up video tracking — page.video is only non-None when record_video_dir was set.
    if launch_options.record_video and page.video is not None:
        new_session._video = page.video

    async def _pool_close_requester() -> Any:
        # Identity-aware: routes through the pool's durable, FIFO-coordinated
        # cutoff instead of tearing the session down directly. force=True
        # matches session.close()'s existing behavior, which never itself
        # respected `protected`.
        return await pool.close(instance_id, force=True, _expected_session=new_session)

    new_session._pool_close_requester = _pool_close_requester
    return new_session


async def _prepare_session_before_publication(
    pool: BrowserPool,
    *,
    recorder: Recorder,
    launch_options: LaunchOptions,
    instance_id: str,
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
) -> BrowserSession:
    """BrowserSession construction, listener/init-script/trace wiring.

    Nothing here can resolve the session by ``instance_id`` yet — it is not
    registry-visible until ``post_context_setup`` publishes it — so none of
    this needs an operation lease.

    ``recorder`` is constructed by the CALLER (as the first statement of
    ``post_context_setup``, restoring the pre-Task-10 ordering) rather than
    here, so that a failure partway through this function's three
    failure-prone awaits (``_expose_viewport_binding``, ``wire_init_scripts``,
    ``context.tracing.start``) still leaves the caller's failure handler
    holding a live reference to close deterministically — a recorder that
    only this function's local variable pointed to would otherwise leak its
    open file handle to GC-timed cleanup instead of a deterministic close.
    """
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
        pool=pool,
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
        operation_queue_timeout_seconds=pool.operation_queue_timeout_seconds,
    )
    new_session.attach_console()
    await new_session.measure_frame_inset(page)
    await pool._expose_viewport_binding(context, new_session)
    # Order matters: the close-evictor and user-nav logger publish handler
    # factories on the session so that subsequent _wire_listeners calls
    # (for popup pages) pick them up automatically. Install them BEFORE
    # the initial _wire_listeners call so the initial page also gets them.
    _wire_close_evictor(pool, new_session)
    _wire_user_navigation_logger(new_session)
    _wire_listeners(new_session, page)
    context.on("page", new_session._register_popup)
    # Chromium redirects new tabs via the service-worker extension (see
    # _build_launch_kwargs) — attaching the page-event redirector there too
    # would race it and uselessly attempt a goto on the detach-prone NTP.
    # Firefox/WebKit have no extension hook, so they use the redirector.
    if kind != "chromium":
        context.on("page", _make_new_tab_redirector(new_session))

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
        viewport_frame_inset_w=new_session.viewport_frame_inset_w,
        viewport_frame_inset_h=new_session.viewport_frame_inset_h,
    )

    if launch_options.trace:
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)

    return new_session
