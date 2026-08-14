# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Launch-pipeline helpers extracted from ``BrowserPool._launch_impl``.

The pipeline is split into four pieces:

- ``launch_publish._prepare_session_before_publication`` — session
  construction, listener/init-script/trace wiring against a ``Recorder`` the
  caller already constructed. Runs BEFORE the session is registry-visible
  (split into its own module to keep this file under the repository's
  550-line LOC ceiling — see that module's docstring).
- ``cleanup_failed_launch`` — unified cleanup for both the pre-register
  context-open phase and the post-register session-setup phase.
- ``post_context_setup`` — registry insertion through initial navigation,
  held under one ``browser_launch_navigation`` operation lease (acquired on
  the not-yet-published session, then held across the publish) so a
  concurrent dashboard/in-process caller can never win a ticket in the gap
  between insertion and the initial goto.
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
from octowright.browser_pool.launch_helpers import _safe_manifest_record
from octowright.browser_pool.launch_publish import (
    _BLANK_URL_PREFIXES,
    _BLANK_URLS,
    _build_session_object,
    _is_blank_newtab_url,
    _make_new_tab_redirector,
    _prepare_session_before_publication,
    _redirect_tasks,
)
from octowright.recorder import Recorder

if TYPE_CHECKING:
    from octowright.browser_pool.options import LaunchOptions
    from octowright.browser_pool.pool import BrowserPool

log = get_logger(__name__)

__all__ = [
    "_BLANK_URLS",
    "_BLANK_URL_PREFIXES",
    "_build_session_object",
    "_is_blank_newtab_url",
    "_make_new_tab_redirector",
    "_redirect_tasks",
    "build_launch_result",
    "cancel_cleanup_launch",
    "cleanup_failed_launch",
    "post_context_setup",
]


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
    """Cancellation AFTER the session was registered: route it through the
    pool's identity-aware durable close coordinator instead of popping
    ``_sessions``, removing the manifest, and closing the session by hand —
    that would bypass the gate cutoff and leave the registries/manifest/event
    bus inconsistent if launch cancellation lands immediately after
    publication. A registered session owns its resources, so a cancelled
    launch that never returned the instance_id to the caller would otherwise
    leak an orphan the caller can't address. Best-effort; logs but does not
    raise. ``_reason="agent_close"`` — this is internal cleanup of an
    agent-requested launch, not a wire-vocabulary change."""
    session = pool.maybe_get(instance_id)
    if session is None:
        return  # a racing external-close eviction already removed it
    try:
        await pool.close(instance_id, force=True, _reason="agent_close", _expected_session=session)
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
    gated by the local ``registered`` flag. The ``Recorder`` is constructed
    HERE, as the first statement in the ``try`` block (matching the
    pre-Task-10 ordering) — not inside ``_prepare_session_before_publication``
    — so that if any of that helper's three failure-prone awaits
    (``_expose_viewport_binding``, ``wire_init_scripts``,
    ``context.tracing.start``) raises, the ``except`` handlers below still
    hold a live reference and can close it deterministically via
    ``cleanup_unregistered_launch`` instead of leaking an open file handle to
    GC-timed cleanup. Everything else before registry publication (session
    construction, listener/init-script/trace wiring) is delegated to
    ``_prepare_session_before_publication`` — nothing there can resolve the
    session by instance_id yet. Registry publication through the initial
    navigation runs under one ``browser_launch_navigation`` lease, ACQUIRED ON
    THE SESSION BEFORE IT IS INSERTED into ``pool._sessions`` and held across
    that insertion — so a concurrent dashboard/in-process caller that
    resolves the instance_id the instant it appears just queues behind this
    same root operation instead of racing the initial goto.
    """
    recorder: Recorder | None = None
    registered = False
    try:
        recorder = Recorder(log_path)
        new_session = await _prepare_session_before_publication(
            pool,
            recorder=recorder,
            launch_options=launch_options,
            instance_id=instance_id,
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

        async with new_session.operation("browser_launch_navigation"):
            # Register the session BEFORE navigating so a failed goto doesn't
            # destroy the browser. A nav error is logged and returned in the
            # result but the instance stays alive and usable.
            async with pool._sessions_lock:
                pool._sessions[instance_id] = new_session
            # From this point cancellation must take the registered-session
            # cleanup path. The manifest transaction is awaited off-thread and
            # can be the first cancellation checkpoint after registry
            # insertion.
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

            # target_url is validated before allocation in
            # BrowserPool._launch_impl, so by here it is known-safe; a goto
            # failure is a real navigation error (logged + returned as
            # nav_warning), not a policy rejection.
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
            # protected must be resolved to a concrete bool before this
            # point — see pool.py's resolve_protected call (same invariant
            # as launch_publish._build_session_object; post_context_setup
            # has a single caller, BrowserPool._launch_impl, which resolves
            # it first).
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
