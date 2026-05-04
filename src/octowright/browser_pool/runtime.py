# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from playwright.async_api import Playwright
from provide.telemetry import get_logger

from ..defaults import (
    DEFAULT_URL,
    DEFAULT_VIEWPORT_H,
    DEFAULT_VIEWPORT_W,
    HEADLESS_DEFAULT,
    SUPPORTED_KINDS,
)
from ..profiles import profile_dir
from ..recorder import Recorder, new_log_path
from ..session import BrowserSession
from ..stabilize import render_stabilize_script
from .errors import maybe_wrap_playwright_error
from .listeners import _wire_close_evictor, _wire_listeners, _wire_user_navigation_logger
from .visuals import (
    _BADGE_POSITION_DEFAULT,
    _BADGE_POSITIONS,
    _BADGE_SCRIPT,
    _TITLE_TAG_SCRIPT,
    _badge_color_for,
    _badge_text_for,
    _tile_args_for_chromium,
    _title_tag_for,
)

log = get_logger(__name__)


class BrowserPool:
    """Owns a single Playwright driver and a dict of active BrowserSession objects.

    One playwright instance is shared across all sessions; each session gets its own
    Browser, BrowserContext, and Page.
    """

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._sessions: dict[str, BrowserSession] = {}
        self._tile_counter: int = 0
        self._session_profile_dirs: dict[tuple[str, str], Path] = {}

    async def _ensure_pw(self) -> Playwright:
        if self._pw is None:
            from .. import pool as _pool

            self._pw = await _pool.async_playwright().start()
        return self._pw

    async def launch(
        self,
        **options: Any,
    ) -> dict[str, Any]:
        kind = options.get("kind", "chromium")
        url = options.get("url")
        headed = options.get("headed")
        label = options.get("label")
        viewport_w = options.get("viewport_w")
        viewport_h = options.get("viewport_h")
        profile = options.get("profile")
        stabilize = options.get("stabilize", False)
        record_video = options.get("record_video", False)
        trace = options.get("trace", False)
        har = options.get("har", False)
        har_path_opt = options.get("har_path")
        har_mode = options.get("har_mode", "minimal")
        har_url_filter = options.get("har_url_filter")
        har_content = options.get("har_content")
        badge = options.get("badge", True)
        badge_position = options.get("badge_position", _BADGE_POSITION_DEFAULT)
        tile = options.get("tile", False)
        ephemeral = options.get("ephemeral", False)
        session = options.get("session", False)

        if kind not in SUPPORTED_KINDS:
            raise ValueError(f"kind must be one of {SUPPORTED_KINDS}, got {kind!r}")
        if badge_position not in _BADGE_POSITIONS:
            raise ValueError(f"badge_position must be one of {sorted(_BADGE_POSITIONS)}, got {badge_position!r}")
        if ephemeral and session:
            raise ValueError("ephemeral and session are mutually exclusive")
        if har_mode not in {"full", "minimal"}:
            raise ValueError("har_mode must be one of ['full', 'minimal']")
        if har_content is not None and har_content not in {"omit", "embed", "attach"}:
            raise ValueError("har_content must be one of ['omit', 'embed', 'attach']")

        if profile is None and label is not None and not ephemeral and not session:
            profile = label

        session_user_data_dir: str | None = None
        if session:
            import tempfile

            session_key = (label or profile or "anon", kind)
            existing = self._session_profile_dirs.get(session_key)
            if existing is None or not existing.exists():
                tmp = Path(tempfile.mkdtemp(prefix=f"octowright-session-{session_key[0]}-{kind}-"))
                self._session_profile_dirs[session_key] = tmp
                existing = tmp
            session_user_data_dir = str(existing)

        pw = await self._ensure_pw()
        browser_type = getattr(pw, kind)
        headless = not headed if headed is not None else HEADLESS_DEFAULT

        explicit_size = viewport_w is not None or viewport_h is not None
        if headless or explicit_size:
            vw = viewport_w or DEFAULT_VIEWPORT_W
            vh = viewport_h or DEFAULT_VIEWPORT_H
            viewport_kwargs: dict[str, Any] = {"viewport": {"width": vw, "height": vh}}
            log_viewport: dict[str, Any] | None = {"w": vw, "h": vh}
        else:
            viewport_kwargs = {"no_viewport": True}
            log_viewport = None

        target_url = url or DEFAULT_URL
        instance_id = uuid.uuid4().hex[:12]
        from .. import pool as _pool

        recordings_dir = _pool.RECORDINGS_DIR
        log_path = new_log_path(recordings_dir, instance_id, label, kind)

        user_data_dir: Path | None = None
        video_dir: Path | None = None
        if record_video:
            video_dir = recordings_dir / "videos" / uuid.uuid4().hex[:8]
            video_dir.mkdir(parents=True, exist_ok=True)

        ctx_video_kwargs: dict[str, Any] = {}
        if video_dir is not None:
            ctx_video_kwargs["record_video_dir"] = str(video_dir)

        har_path: Path | None = None
        if har or har_path_opt:
            har_path = Path(har_path_opt) if har_path_opt else log_path.with_suffix(".har")
            if not har_path.is_absolute():
                har_path = (recordings_dir / har_path).resolve()
            har_path.parent.mkdir(parents=True, exist_ok=True)

        ctx_har_kwargs: dict[str, Any] = {}
        if har_path is not None:
            ctx_har_kwargs["record_har_path"] = str(har_path)
            ctx_har_kwargs["record_har_mode"] = har_mode
            if har_url_filter:
                ctx_har_kwargs["record_har_url_filter"] = har_url_filter
            if har_content:
                ctx_har_kwargs["record_har_content"] = har_content

        launch_kwargs: dict[str, Any] = {}
        if tile and kind == "chromium" and not headless:
            tile_index = self._tile_counter
            self._tile_counter += 1
            launch_kwargs["args"] = _tile_args_for_chromium(tile_index)

        try:
            if profile or session_user_data_dir:
                if profile:
                    pdir = profile_dir(kind, profile)
                    pdir.mkdir(parents=True, exist_ok=True)
                    user_data_dir = pdir
                else:
                    user_data_dir = Path(session_user_data_dir) if session_user_data_dir else None
                context = await browser_type.launch_persistent_context(
                    str(user_data_dir),
                    headless=headless,
                    accept_downloads=True,
                    **viewport_kwargs,
                    **ctx_video_kwargs,
                    **ctx_har_kwargs,
                    **launch_kwargs,
                )
                browser = None
                page = context.pages[0] if context.pages else await context.new_page()
            else:
                browser = await browser_type.launch(headless=headless, **launch_kwargs)
                context = await browser.new_context(
                    accept_downloads=True,
                    **viewport_kwargs,
                    **ctx_video_kwargs,
                    **ctx_har_kwargs,
                )
                page = await context.new_page()
        except Exception as exc:
            wrapped = maybe_wrap_playwright_error(exc, kind=kind)
            raise wrapped from exc
        recorder = Recorder(log_path)
        recorder.record(
            "launch",
            instance_id=instance_id,
            kind=kind,
            label=label,
            profile=profile,
            user_data_dir=str(user_data_dir) if user_data_dir else None,
            url=target_url,
            headed=not headless,
            viewport=log_viewport,
            stabilize=stabilize,
            record_video=record_video,
            video_dir=str(video_dir) if video_dir else None,
            trace=trace,
            har=bool(har_path),
            har_path=str(har_path) if har_path else None,
            har_mode=har_mode if har_path else None,
            har_url_filter=har_url_filter if har_path else None,
            har_content=har_content if har_path else None,
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
            user_data_dir=user_data_dir,
            profile=profile,
            stabilize=stabilize,
            trace=trace,
            har_path=har_path,
        )
        if record_video and page.video is not None:
            new_session._video = page.video
        new_session.attach_console()
        _wire_close_evictor(self, new_session)
        _wire_user_navigation_logger(new_session)
        _wire_listeners(new_session, page)
        context.on("page", new_session._register_popup)

        persona_emoji_override: str | None = None
        if profile:
            try:
                from ..personas import load_persona

                persona_emoji_override = load_persona(profile).emoji
            except FileNotFoundError:
                pass

        title_tag = _title_tag_for(profile, label, persona_emoji=persona_emoji_override, kind=kind)
        if title_tag:
            script = _TITLE_TAG_SCRIPT.replace("__SUFFIX__", json.dumps(title_tag))
            await context.add_init_script(script=script)
        if badge:
            badge_text = _badge_text_for(profile, label, instance_id, persona_emoji=persona_emoji_override, kind=kind)
            color_seed = profile or label or instance_id[:6]
            badge_script = (
                _BADGE_SCRIPT.replace("__TAG__", json.dumps(badge_text))
                .replace("__COLOR__", json.dumps(_badge_color_for(color_seed)))
                .replace("__POS__", json.dumps(_BADGE_POSITIONS[badge_position]))
            )
            await context.add_init_script(script=badge_script)
        if stabilize:
            await context.add_init_script(script=render_stabilize_script())

        if trace:
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        try:
            await page.goto(target_url)
        except Exception as exc:
            wrapped = maybe_wrap_playwright_error(exc, kind=kind)
            raise wrapped from exc
        new_session._schedule_markdown_capture()

        self._sessions[instance_id] = new_session
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

    def get(self, instance_id: str) -> BrowserSession:
        if instance_id not in self._sessions:
            known = list(self._sessions)
            hint = (
                "no browsers are live — call browser_launch first"
                if not known
                else f"call browser_list to see live ids; known: {known}"
            )
            raise KeyError(f"no browser with instance_id={instance_id!r}; {hint}")
        return self._sessions[instance_id]

    def list_sessions(self) -> list[dict[str, Any]]:
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
            for s in self._sessions.values()
        ]

    def profile_in_use(self, kind: str, profile: str) -> bool:
        return any(s.kind == kind and s.profile == profile for s in self._sessions.values())

    async def close(self, instance_id: str) -> dict[str, Any]:
        session = self.get(instance_id)
        del self._sessions[instance_id]
        await session.close()
        log.info(
            "octowright.browser.closed",
            instance_id=instance_id,
            kind=session.kind,
            profile=session.profile,
            log_path=str(session.log_path),
        )
        return {
            "closed": True,
            "log_path": str(session.log_path),
            "video_path": str(session.video_path) if session.video_path else None,
            "trace_path": str(session.trace_path) if session.trace_path else None,
            "har_path": str(session.har_path) if session.har_path else None,
        }

    async def handoff(
        self,
        instance_id: str,
        *,
        headed: bool,
        close_original: bool = True,
        accept_stateless: bool = False,
        url: str | None = None,
        label: str | None = None,
        profile: str | None = None,
        viewport_w: int | None = None,
        viewport_h: int | None = None,
        stabilize: bool | None = None,
        record_video: bool = False,
        trace: bool = False,
        badge: bool = True,
        badge_position: str = _BADGE_POSITION_DEFAULT,
        tile: bool = False,
    ) -> dict[str, Any]:
        source = self.get(instance_id)
        had_persistent_state = bool(source.profile or source.user_data_dir)
        if not had_persistent_state and not accept_stateless:
            raise ValueError(
                "handoff would be stateless: source has no profile/user_data_dir; pass accept_stateless=True to proceed"
            )
        if not close_original and had_persistent_state:
            raise ValueError(
                "persistent handoff requires close_original=True so the profile directory can be safely reused"
            )

        source_label = source.label
        source_profile = source.profile
        target_profile = profile if profile is not None else source_profile
        target_label = label if label is not None else source_label
        target_url = url if url is not None else source.url
        if not isinstance(target_url, str) or not target_url.strip():
            target_url = source.page.url or source.url
        if not isinstance(target_url, str) or not target_url.strip():
            target_url = DEFAULT_URL

        source_is_session_tmpdir = source.user_data_dir is not None and source.profile is None

        if close_original:
            await self.close(instance_id)

        launch = await self.launch(
            kind=source.kind,
            url=target_url,
            headed=headed,
            label=target_label,
            profile=target_profile,
            viewport_w=viewport_w,
            viewport_h=viewport_h,
            stabilize=source.stabilize if stabilize is None else stabilize,
            record_video=record_video,
            trace=trace,
            har=bool(source.har_path),
            har_path=str(source.har_path) if source.har_path else None,
            badge=badge,
            badge_position=badge_position,
            tile=tile,
            session=source_is_session_tmpdir and target_profile is None,
        )
        return {
            "old_instance_id": instance_id,
            "new_instance_id": launch["instance_id"],
            "old_closed": close_original,
            "kind": launch["kind"],
            "headed": headed,
            "profile": launch.get("profile"),
            "label": launch.get("label"),
            "url": launch.get("url"),
            "record_video": launch.get("record_video", False),
            "trace": launch.get("trace", False),
            "har_path": launch.get("har_path"),
        }

    async def close_all(self) -> dict[str, Any]:
        ids = list(self._sessions.keys())
        for iid in ids:
            await self.close(iid)
        return {"closed": ids}

    async def spawn_roster(self, specs: list[dict[str, Any]]) -> dict[str, Any]:
        """Launch N browsers concurrently from a list of launch spec dicts.

        Each spec may contain any subset of: kind, url, headed, label, profile,
        viewport_w, viewport_h, stabilize, record_video. Runs with asyncio.gather
        so they boot in parallel; an error on one browser does not abort others.

        Returns {"launched": [launch_result, ...], "errors": [{"spec": ..., "error": "..."}, ...]}.
        """

        async def _launch_one(spec: dict[str, Any]) -> dict[str, Any]:
            return await self.launch(
                kind=spec.get("kind", "chromium"),
                url=spec.get("url"),
                headed=spec.get("headed", True),
                label=spec.get("label"),
                viewport_w=spec.get("viewport_w"),
                viewport_h=spec.get("viewport_h"),
                profile=spec.get("profile"),
                record_video=spec.get("record_video", False),
                stabilize=spec.get("stabilize", False),
                trace=spec.get("trace", False),
                har=spec.get("har", False),
                har_path=spec.get("har_path"),
                har_mode=spec.get("har_mode", "minimal"),
                har_url_filter=spec.get("har_url_filter"),
                har_content=spec.get("har_content"),
                badge=spec.get("badge", True),
                badge_position=spec.get("badge_position", _BADGE_POSITION_DEFAULT),
                tile=spec.get("tile", False),
                ephemeral=spec.get("ephemeral", False),
                session=spec.get("session", False),
            )

        results = await asyncio.gather(*[_launch_one(s) for s in specs], return_exceptions=True)
        launched: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for spec, result in zip(specs, results, strict=True):
            if isinstance(result, BaseException):
                errors.append({"spec": spec, "error": str(result)})
            else:
                launched.append(result)
        return {"launched": launched, "errors": errors}

    async def shutdown(self) -> None:
        import shutil as _shutil

        await self.close_all()
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        for tmpdir in self._session_profile_dirs.values():
            try:
                _shutil.rmtree(tmpdir, ignore_errors=True)
            except OSError:
                pass
        self._session_profile_dirs.clear()
