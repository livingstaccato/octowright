# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from playwright.async_api import Playwright, async_playwright
from provide.telemetry import get_logger

from octowright._tracing import set_attrs, span
from octowright.browser_pool._metrics import launch_span
from octowright.browser_pool.cleanup import cleanup_on_launch_failure
from octowright.browser_pool.errors import maybe_wrap_playwright_error
from octowright.browser_pool.events import SessionCloseReason
from octowright.browser_pool.launch_helpers import (
    _build_har_kwargs,
    _build_video_kwargs,
    _build_viewport_kwargs,
    _open_browser_context,
    rotate_har_path,
)
from octowright.browser_pool.launch_pipeline import cleanup_failed_launch, post_context_setup
from octowright.browser_pool.lifecycle import close_browser, handoff_browser, shutdown_pool
from octowright.browser_pool.options import LaunchOptions
from octowright.browser_pool.roster import close_all as _close_all
from octowright.browser_pool.roster import spawn_roster as _spawn_roster
from octowright.browser_pool.visuals import _tile_args_for_chromium
from octowright.defaults import DEFAULT_URL, HEADLESS_DEFAULT, RECORDINGS_DIR
from octowright.recorder import new_log_path
from octowright.session import BrowserSession

log = get_logger(__name__)

_safe_cleanup_on_launch_failure = cleanup_on_launch_failure


class BrowserPool:
    """Owns a single Playwright driver and a dict of active BrowserSession objects.

    One playwright instance is shared across all sessions; each session gets its own
    Browser, BrowserContext, and Page.
    """

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._pw_lock = asyncio.Lock()
        self._sessions: dict[str, BrowserSession] = {}
        self._sessions_lock = asyncio.Lock()
        # Monotonic counter for window-tile slot assignment. Reading
        # len(_sessions) at launch time would race when N launches run in
        # parallel — they'd all see the same count and grab the same slot.
        # _tile_lock guards the read+increment so concurrent spawn_roster
        # coroutines don't both observe the same slot before either bumps it.
        self._tile_counter: int = 0
        self._tile_lock = asyncio.Lock()
        # session=True profile dirs: tmpdirs that live for the daemon's
        # lifetime. Keyed by (session_key, kind) so the same label across
        # engines gets independent jars (matching real persistent semantics).
        self._session_profile_dirs: dict[tuple[str, str], Path] = {}

    async def _ensure_pw(self) -> Playwright:
        async with self._pw_lock:
            if self._pw is None:
                self._pw = await async_playwright().start()
        return self._pw

    async def launch(self, **options: Any) -> dict[str, Any]:
        async with launch_span(options.get("kind") or "chromium") as sp:
            return await self._launch_impl(options, sp)

    async def _launch_impl(self, options: dict[str, Any], _sp: Any) -> dict[str, Any]:
        launch_options = LaunchOptions.from_mapping(options)
        kind = launch_options.kind
        headed = launch_options.headed
        label = launch_options.label
        session = launch_options.session

        instance_id = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()

        # Promote: a named launch (label given, no explicit profile, not ephemeral
        # and not session-scoped) gets a persistent profile by default. The whole
        # reason for naming a browser is so you can come back to it; ephemeral
        # and session are the explicit exceptions.
        profile = launch_options.promoted_profile()

        session_user_data_dir = await self._resolve_session_dir(session, launch_options, instance_id, kind)
        set_attrs(_sp, instance_id=instance_id, profile=profile, label=label, session=session)
        pw = await self._ensure_pw()
        browser_type = getattr(pw, kind)
        headless = not headed if headed is not None else HEADLESS_DEFAULT
        target_url = launch_options.url or DEFAULT_URL
        log_path = new_log_path(RECORDINGS_DIR, instance_id, label, kind)

        viewport_kwargs, log_viewport, explicit_size, viewport_info = _build_viewport_kwargs(
            headless, launch_options.viewport_w, launch_options.viewport_h
        )
        ctx_video_kwargs, video_dir = _build_video_kwargs(
            launch_options.record_video, headless, explicit_size, launch_options.viewport_w, launch_options.viewport_h
        )
        har_path, ctx_har_kwargs = _build_har_kwargs(
            har=launch_options.har,
            har_path_opt=launch_options.har_path,
            har_mode=launch_options.har_mode,
            har_url_filter=launch_options.har_url_filter,
            har_content=launch_options.har_content,
            log_path=log_path,
        )
        launch_kwargs = await self._build_launch_kwargs(tile=launch_options.tile, kind=kind, headless=headless)

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
            self,
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

    def get(self, instance_id: str) -> BrowserSession:
        """Return the live session for ``instance_id`` or raise KeyError.

        Reads ``_sessions`` without ``_sessions_lock`` — concurrent eviction
        (``_evict_session_nowait`` in ``listeners.py``) writes without the
        lock too, relying on CPython's GIL-atomic ``dict.pop``. The returned
        session may therefore be evicted by a Playwright-side close signal
        between this call and the caller's first ``await`` on it; tool
        handlers in that window will surface as Playwright-disconnected
        errors rather than a clean KeyError. (The eviction itself has
        already incremented ``octowright_browser_evicted_total`` via the
        listener path.) The caller is expected to treat both as terminal
        for the session and either close or retry.
        """
        if instance_id not in self._sessions:
            raise KeyError(self._missing_session_message(instance_id))
        return self._sessions[instance_id]

    def _missing_session_message(self, instance_id: str) -> str:
        known = list(self._sessions)
        hint = (
            "no browsers are live — call browser_launch first"
            if not known
            else f"call browser_list to see live ids; known: {known}"
        )
        return f"no browser with instance_id={instance_id!r}; {hint}"

    def maybe_get(self, instance_id: str) -> BrowserSession | None:
        return self._sessions.get(instance_id)

    def has_session(self, instance_id: str) -> bool:
        return instance_id in self._sessions

    def iter_sessions(self) -> Iterable[BrowserSession]:
        return tuple(self._sessions.values())

    def active_count(self) -> int:
        return len(self._sessions)

    def list_sessions(self) -> list[dict[str, Any]]:
        # Snapshot values() into a tuple before iterating: Playwright sync
        # close callbacks fire _evict_session_nowait between awaits and could
        # otherwise mutate the dict mid-iteration.
        return [
            {
                "instance_id": s.instance_id,
                "kind": s.kind,
                "label": s.label,
                "profile": s.profile,
                "url": s.url,
                "log_path": str(s.log_path),
                "har_path": str(s.har_path) if s.har_path else None,
            }
            for s in tuple(self._sessions.values())
        ]

    async def _expose_viewport_binding(self, context: Any, session: BrowserSession) -> None:
        expose_binding = getattr(context, "expose_binding", None)
        if expose_binding is None:
            return

        async def _viewport_action(_source: Any, payload: dict[str, Any]) -> dict[str, Any]:
            action = payload.get("action")
            if action == "sync":
                return await session.viewport_sync()
            if action == "relaunch-fluid":
                return await self.relaunch_fluid(session.instance_id)
            raise ValueError(f"unknown viewport action: {action!r}")

        await expose_binding("__octowright_viewport_action", _viewport_action)

    async def relaunch_fluid(self, instance_id: str) -> dict[str, Any]:
        source = self.get(instance_id)
        # Snapshot every field we need BEFORE awaiting close. A Playwright
        # external-close eviction can fire between pool.get() and pool.close(),
        # popping the session and turning close() into a KeyError. We treat
        # that race as "already closed" and still launch the replacement so
        # the user isn't left with no browser.
        source_kind = source.kind
        source_label = source.label
        source_profile = source.profile
        source_user_data_dir = source.user_data_dir
        source_stabilize = source.stabilize
        source_trace = source.trace
        source_har_path = source.har_path
        target_url = getattr(source.page, "url", None) or source.url
        # Wrap close+launch under a parent span so the child browser.close /
        # browser.launch spans nest underneath as one fluid-mode round-trip.
        with span("octowright.browser.relaunch_fluid", instance_id=instance_id, kind=source_kind):
            session_scoped = source_profile is None and source_user_data_dir is not None
            stateless = source_profile is None and source_user_data_dir is None
            # Don't overwrite the prior HAR — relaunch gets a sibling path.
            next_har = rotate_har_path(source_har_path)
            try:
                close_result: dict[str, Any] | None = await self.close(instance_id)
            except KeyError:
                log.warning(
                    "octowright.browser.relaunch_fluid.close_raced_eviction",
                    instance_id=instance_id,
                    kind=source_kind,
                )
                close_result = None
            result = await self.launch(
                kind=source_kind,
                url=target_url,
                headed=True,
                label=source_label,
                profile=source_profile,
                stabilize=source_stabilize,
                trace=source_trace,
                har=bool(source_har_path),
                har_path=str(next_har) if next_har else None,
                badge=True,
                ephemeral=stateless,
                session=session_scoped,
            )
            return {
                "ok": True,
                "old_instance_id": instance_id,
                "new_instance_id": result["instance_id"],
                "old_closed": bool(close_result and close_result.get("closed")),
                "mode": "fluid",
                "launch": result,
            }

    def profile_in_use(self, kind: str, profile: str) -> bool:
        return any(s.kind == kind and s.profile == profile for s in tuple(self._sessions.values()))

    def _evict_session_nowait(self, instance_id: str) -> BrowserSession | None:
        # Called from synchronous Playwright event callbacks (page.close,
        # context.close, browser.disconnected). Can't `await` a lock from a
        # sync callback, but CPython dict.pop is GIL-atomic and asyncio is
        # single-threaded — so this and the locked pop in close_browser
        # cannot interleave in flight. Idempotent: returns None on miss.
        return self._sessions.pop(instance_id, None)

    async def close(self, instance_id: str, *, _reason: SessionCloseReason = "agent_close") -> dict[str, Any]:
        return await close_browser(self, instance_id, _reason=_reason)

    async def close_all(self, *, _reason: SessionCloseReason = "agent_close") -> dict[str, Any]:
        return await _close_all(self, _reason=_reason)

    async def handoff(
        self,
        old_instance_id: str,
        *,
        headed: bool | None = None,
        close_original: bool = True,
        accept_stateless: bool = False,
    ) -> dict[str, Any]:
        return await handoff_browser(
            self,
            old_instance_id,
            headed=headed,
            close_original=close_original,
            accept_stateless=accept_stateless,
        )

    async def spawn_roster(self, specs: list[dict[str, Any]]) -> dict[str, Any]:
        """Launch N browsers concurrently from a list of launch spec dicts.

        Each spec may contain any subset of: kind, url, headed, label, profile,
        viewport_w, viewport_h, stabilize, record_video.  Runs with
        asyncio.gather so they boot in parallel.  An error on one browser does
        NOT abort the others.

        Returns {"launched": [launch_result, ...], "errors": [{"spec": ..., "error": "..."}, ...]}.
        """

        return await _spawn_roster(self, specs)

    async def shutdown(self) -> None:
        await shutdown_pool(self)

    async def _build_launch_kwargs(self, *, tile: bool, kind: str, headless: bool) -> dict[str, Any]:
        """Chromium-only window tiling. Holds ``_tile_lock`` only for the
        read+increment of ``_tile_counter`` so parallel spawn_roster launches
        don't share the same slot. No-op for firefox/webkit (no equivalent CLI
        hook) and headless runs."""
        out: dict[str, Any] = {}
        if tile and kind == "chromium" and not headless:
            async with self._tile_lock:
                tile_index = self._tile_counter
                self._tile_counter += 1
            out["args"] = _tile_args_for_chromium(tile_index)
        return out

    async def _resolve_session_dir(
        self,
        session: bool,
        launch_options: LaunchOptions,
        instance_id: str,
        kind: str,
    ) -> str | None:
        """Session=True: a tmpdir profile that lives for the daemon's
        lifetime, reused across launches sharing the same (session_key, kind).
        Named sessions reuse by label; anonymous sessions key by instance_id
        so unrelated callers never share state.

        Concurrency: ``spawn_roster`` gathers _launch_one coroutines that all
        funnel here; without serialisation two same-(label, kind) coros could
        each call ``mkdtemp`` and leak the loser. ``_sessions_lock`` collapses
        the read-or-create critical section so exactly one tmpdir per key is
        ever minted."""
        if not session:
            return None
        import tempfile

        session_name = launch_options.session_name(instance_id)
        session_key = (session_name, kind)
        async with self._sessions_lock:
            existing = self._session_profile_dirs.get(session_key)
            if existing is None or not existing.exists():
                tmp = Path(tempfile.mkdtemp(prefix=f"octowright-session-{session_name}-{kind}-"))
                self._session_profile_dirs[session_key] = tmp
                existing = tmp
        return str(existing)
