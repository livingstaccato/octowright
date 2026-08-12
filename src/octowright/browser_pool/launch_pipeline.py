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


def _make_new_tab_redirector() -> Any:
    """Return a sync page-event handler that redirects blank new tabs to /new-tab.

    Waits for domcontentloaded (up to 800 ms) so the URL is settled before
    checking — more reliable than a fixed sleep. This is the Firefox/WebKit
    path (and a Chromium fallback); Chromium normally never reaches the goto
    because the new-tab override extension already replaced the NTP.
    """

    def _on_new_page(new_page: Any) -> None:
        async def _redirect() -> None:
            from octowright.defaults import get_default_url

            # Only redirect user-opened tabs (Cmd+T), never programmatic popups.
            # A window.open(...) popup has an opener page; a fresh Cmd+T tab does
            # not. Skipping opened popups leaves app-controlled windows alone.
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

        task = asyncio.create_task(_redirect())
        _redirect_tasks.add(task)
        task.add_done_callback(_redirect_tasks.discard)

    return _on_new_page


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


async def cancel_cleanup_after_register(pool: Any, instance_id: str) -> None:
    """Cancellation AFTER the session was registered: atomically remove it from
    the pool and tear it down. A registered session owns its resources, so the
    ``registered``-gated cleanup skips it — but a cancelled launch never returns
    the instance_id to the caller, so without this the live browser is an
    orphan the caller can't address. Best-effort; logs but does not raise."""
    async with pool._sessions_lock:
        session = pool._sessions.pop(instance_id, None)
    if session is None:
        return  # a racing external-close eviction already removed it
    try:
        from octowright.session_manifest import remove_session as _manifest_remove_session
        from octowright.session_manifest import run_manifest_transaction_async

        await run_manifest_transaction_async(_manifest_remove_session, instance_id)
    except Exception as exc:
        log.warning("octowright.session_manifest.remove_failed", instance_id=instance_id, error=repr(exc))
    try:
        await session.close()
    except Exception as exc:
        log.warning("octowright.launch.cancel_close_failed", instance_id=instance_id, error=repr(exc))


async def cancel_cleanup_launch(
    *,
    registered: bool,
    pool: Any,
    instance_id: str,
    context: Any,
    browser: Any,
    video_dir: Path | None,
    recorder: Recorder | None,
) -> None:
    """Complete launch rollback despite level or repeated cancellation."""
    from octowright.session_manifest import wait_task_after_cancellation

    if (current := asyncio.current_task()) is not None:
        current.uncancel()
    if registered:
        cleanup_task = asyncio.create_task(cancel_cleanup_after_register(pool, instance_id))
    else:
        cleanup_task = asyncio.create_task(
            cleanup_failed_launch(
                registered=False,
                context=context,
                browser=browser,
                video_dir=video_dir,
                recorder=recorder,
                pre_register=False,
            )
        )
    await wait_task_after_cancellation(cleanup_task)


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
    protected: bool = False,
    protected_reason: str = "explicit",
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
        "protected": protected,
        "protected_reason": protected_reason,
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
    # protected must be resolved to a concrete bool before this point — the
    # only caller (post_context_setup) is only ever invoked from
    # BrowserPool._launch_impl, which calls resolve_protected() and rebinds
    # launch_options via dataclasses.replace() before handing off here. See
    # pool.py's resolve_protected call.
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
        # Chromium redirects new tabs via the service-worker extension (see
        # _build_launch_kwargs) — attaching the page-event redirector there too
        # would race it and uselessly attempt a goto on the detach-prone NTP.
        # Firefox/WebKit have no extension hook, so they use the redirector.
        if kind != "chromium":
            context.on("page", _make_new_tab_redirector())

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

        # Register the session BEFORE navigating so a failed goto doesn't
        # destroy the browser. A nav error is logged and returned in the result
        # but the instance stays alive and usable.
        async with pool._sessions_lock:
            pool._sessions[instance_id] = new_session
        # From this point cancellation must take the registered-session cleanup
        # path. The manifest transaction is awaited off-thread and can be the
        # first cancellation checkpoint after registry insertion.
        registered = True
        await _safe_manifest_record(
            instance_id=instance_id,
            kind=kind,
            label=label,
            profile=profile,
            user_data_dir=user_data_dir,
            log_path=log_path,
        )
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

        # target_url is validated before allocation in BrowserPool._launch_impl,
        # so by here it is known-safe; a goto failure is a real navigation error
        # (logged + returned as nav_warning), not a policy rejection.
        nav_error: str | None = None
        try:
            await page.goto(target_url)
        except Exception as _nav_exc:
            nav_error = str(_nav_exc)
            log.warning(
                "octowright.browser.launch_nav_failed",
                instance_id=instance_id,
                url=target_url,
                error=nav_error,
            )

        new_session._schedule_markdown_capture()
        # protected must be resolved to a concrete bool before this point —
        # see pool.py's resolve_protected call (same invariant as
        # _build_session_object above; post_context_setup has a single
        # caller, BrowserPool._launch_impl, which resolves it first).
        assert launch_options.protected is not None, (  # nosec B101  # narrow for type-checker
            "protected must be resolved to a concrete bool before this point — see pool.py's resolve_protected call"
        )
        result = build_launch_result(
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
            protected=launch_options.protected,
            protected_reason=launch_options.protected_reason,
        )
        if nav_error is not None:
            result["nav_warning"] = nav_error
        return result
    except asyncio.CancelledError:
        # Join a detached rollback task despite persistent AnyIO cancellation
        # or a second direct Task.cancel (for example request teardown followed
        # by daemon shutdown). Never leave a popped session unclosed.
        await cancel_cleanup_launch(
            registered=registered,
            pool=pool,
            instance_id=instance_id,
            context=context,
            browser=browser,
            video_dir=video_dir,
            recorder=recorder,
        )
        raise
    except Exception as exc:
        await cleanup_failed_launch(
            registered=registered,
            context=context,
            browser=browser,
            video_dir=video_dir,
            recorder=recorder,
            pre_register=False,
        )
        wrapped = maybe_wrap_playwright_error(exc, kind=kind)
        if wrapped is exc:
            raise
        raise wrapped from exc
