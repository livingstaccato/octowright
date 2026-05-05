# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from playwright.async_api import Playwright, async_playwright
from provide.telemetry import get_logger

from .browser_pool.errors import maybe_wrap_playwright_error
from .defaults import (
    DEFAULT_URL,
    DEFAULT_VIEWPORT_H,
    DEFAULT_VIEWPORT_W,
    HEADLESS_DEFAULT,
    RECORDINGS_DIR,
    SUPPORTED_KINDS,
)
from .pool_roster import close_all as _close_all
from .pool_roster import spawn_roster as _spawn_roster
from .pool_support import (
    _BADGE_POSITION_DEFAULT,
    _BADGE_POSITIONS,
    _BADGE_SCRIPT,
    _TITLE_TAG_SCRIPT,
    _badge_color_for,
    _badge_text_for,
    _tile_args_for_chromium,
    _title_tag_for,
    _wire_close_evictor,
    _wire_listeners,
    _wire_user_navigation_logger,
)
from .profiles import profile_dir
from .recorder import Recorder, new_log_path
from .session import BrowserSession
from .stabilize import render_stabilize_script

log = get_logger(__name__)


class BrowserPool:
    """Owns a single Playwright driver and a dict of active BrowserSession objects.

    One playwright instance is shared across all sessions; each session gets its own
    Browser, BrowserContext, and Page.
    """

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._sessions: dict[str, BrowserSession] = {}
        # Monotonic counter for window-tile slot assignment. Reading
        # len(_sessions) at launch time would race when N launches run in
        # parallel — they'd all see the same count and grab the same slot.
        # The counter is incremented synchronously at the start of launch().
        self._tile_counter: int = 0
        # session=True profile dirs: tmpdirs that live for the daemon's
        # lifetime. Keyed by (session_key, kind) so the same label across
        # engines gets independent jars (matching real persistent semantics).
        self._session_profile_dirs: dict[tuple[str, str], Path] = {}

    async def _ensure_pw(self) -> Playwright:
        if self._pw is None:
            self._pw = await async_playwright().start()
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

        browser: Any | None = None
        context: Any | None = None
        page: Any | None = None
        recorder: Recorder | None = None
        registered = False

        # Promote: a named launch (label given, no explicit profile, not ephemeral
        # and not session-scoped) gets a persistent profile by default. The whole
        # reason for naming a browser is so you can come back to it; ephemeral
        # and session are the explicit exceptions.
        if profile is None and label is not None and not ephemeral and not session:
            profile = label

        # Session-scoped: tmpdir profile that lives for the daemon's lifetime.
        # Reused across launches with the same (session_key, kind) — so closing
        # and reopening keeps state, but daemon shutdown wipes everything.
        # Keyed off label (or "anon" if neither label nor profile given).
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
        target_url = url or DEFAULT_URL
        instance_id = uuid.uuid4().hex[:12]
        log_path = new_log_path(RECORDINGS_DIR, instance_id, label, kind)

        # Headed: when neither viewport_w nor viewport_h is given, let Playwright
        # adopt the OS window size (no_viewport=True) so the page can resize
        # naturally with the window. Caller can still pin a size by passing one
        # or both. Headless still needs a fixed viewport — that's the rendering
        # target — so we keep the defaults there.
        explicit_size = viewport_w is not None or viewport_h is not None
        if headless or explicit_size:
            vw = viewport_w or DEFAULT_VIEWPORT_W
            vh = viewport_h or DEFAULT_VIEWPORT_H
            viewport_kwargs: dict[str, Any] = {"viewport": {"width": vw, "height": vh}}
            log_viewport: dict[str, Any] | None = {"w": vw, "h": vh}
        else:
            viewport_kwargs = {"no_viewport": True}
            log_viewport = None

        user_data_dir: str | None = None
        video_dir: Path | None = None
        if record_video:
            # We need a log_path parent to nest the videos/ dir, but log_path is
            # created after context. Use a temp dir under RECORDINGS_DIR/videos/.
            video_dir = RECORDINGS_DIR / "videos" / uuid.uuid4().hex[:8]
            video_dir.mkdir(parents=True, exist_ok=True)

        ctx_video_kwargs: dict[str, Any] = {}
        if video_dir is not None:
            ctx_video_kwargs["record_video_dir"] = str(video_dir)

        har_path: Path | None = None
        if har or har_path_opt:
            har_path = Path(har_path_opt) if har_path_opt else log_path.with_suffix(".har")
            if not har_path.is_absolute():
                har_path = (RECORDINGS_DIR / har_path).resolve()
            har_path.parent.mkdir(parents=True, exist_ok=True)

        ctx_har_kwargs: dict[str, Any] = {}
        if har_path is not None:
            ctx_har_kwargs["record_har_path"] = str(har_path)
            ctx_har_kwargs["record_har_mode"] = har_mode
            if har_url_filter:
                ctx_har_kwargs["record_har_url_filter"] = har_url_filter
            if har_content:
                ctx_har_kwargs["record_har_content"] = har_content

        # Chromium-only window tiling: deterministic grid based on a monotonic
        # counter incremented before any await — safe under parallel launches.
        # No-op for firefox/webkit (no equivalent CLI hook) and headless runs.
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
                    user_data_dir = str(pdir)
                else:
                    # Session-scoped tmpdir branch — same launch_persistent_context
                    # mechanism as a real persona profile, but the dir lives only
                    # for the daemon's lifetime.
                    user_data_dir = session_user_data_dir
                context = await browser_type.launch_persistent_context(
                    user_data_dir,
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
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            wrapped = maybe_wrap_playwright_error(exc, kind=kind)
            if wrapped is exc:
                raise
            raise wrapped from exc

        try:
            recorder = Recorder(log_path)
            recorder.record(
                "launch",
                instance_id=instance_id,
                kind=kind,
                label=label,
                profile=profile,
                user_data_dir=user_data_dir,
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

            # NOTE: this local was named ``session`` for years, but ``session`` is
            # now the public name of the launch flag (session=True for tmpdir
            # profiles). Renamed to ``new_session`` to avoid shadowing the bool.
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
                stabilize=stabilize,
                trace=trace,
                har_path=har_path,
            )
            # Wire up video tracking — page.video is only non-None when record_video_dir was set.
            if record_video and page.video is not None:
                new_session._video = page.video
            new_session.attach_console()
            # Order matters: the close-evictor and user-nav logger publish handler
            # factories on the session so that subsequent _wire_listeners calls
            # (for popup pages) pick them up automatically. Install them BEFORE
            # the initial _wire_listeners call so the initial page also gets them.
            _wire_close_evictor(self, new_session)
            _wire_user_navigation_logger(new_session)
            _wire_listeners(new_session, page)
            context.on("page", new_session._register_popup)

            # Look up the persona's emoji override (if any) so title + badge can
            # show it. Ephemeral / unknown personas just hash-pick from the pool.
            persona_emoji_override: str | None = None
            if profile:
                try:
                    from .personas import load_persona

                    persona_emoji_override = load_persona(profile).emoji
                except FileNotFoundError:
                    pass

            title_tag = _title_tag_for(profile, label, persona_emoji=persona_emoji_override, kind=kind)
            if title_tag:
                script = _TITLE_TAG_SCRIPT.replace("__SUFFIX__", json.dumps(title_tag))
                await context.add_init_script(script=script)
            if badge:
                badge_text = _badge_text_for(
                    profile, label, instance_id, persona_emoji=persona_emoji_override, kind=kind
                )
                # Color seed is persona-stable: identical across engines so the
                # same persona launched in chromium + firefox + webkit shares one
                # color. Engine differentiation rests on the engine emoji.
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

            await page.goto(target_url)

            new_session._schedule_markdown_capture()

            self._sessions[instance_id] = new_session
            registered = True
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
        except Exception as exc:
            if not registered:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                if recorder is not None:
                    try:
                        recorder.close()
                    except Exception:
                        pass
            wrapped = maybe_wrap_playwright_error(exc, kind=kind)
            if wrapped is exc:
                raise
            raise wrapped from exc

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

    def maybe_get(self, instance_id: str) -> BrowserSession | None:
        return self._sessions.get(instance_id)

    def has_session(self, instance_id: str) -> bool:
        return instance_id in self._sessions

    def iter_sessions(self) -> Iterable[BrowserSession]:
        return tuple(self._sessions.values())

    def active_count(self) -> int:
        return len(self._sessions)

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
        # Remove from the registry BEFORE awaiting session.close() — that call
        # triggers context.close() which fires the close event our external
        # evictor listens for. By the time the evictor runs, _sessions.pop()
        # will return None and the evictor will silently no-op, leaving us as
        # the sole logger of an explicit close.
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

    async def close_all(self) -> dict[str, Any]:
        return await _close_all(self)

    async def handoff(
        self,
        old_instance_id: str,
        *,
        headed: bool | None = None,
        close_original: bool = True,
        accept_stateless: bool = False,
    ) -> dict[str, Any]:
        source = self.get(old_instance_id)
        source_profile = source.profile
        source_user_data_dir = getattr(source, "user_data_dir", None)
        if source_profile is None and source_user_data_dir is None and not accept_stateless:
            raise ValueError(
                "handoff would be stateless: source has no profile/user_data_dir; pass accept_stateless=True to proceed"
            )
        if not close_original and source_profile is not None:
            raise ValueError("persistent handoff requires close_original=True so the profile can be safely reused")

        target_url = getattr(source.page, "url", None) or source.url
        close_result: dict[str, Any] | None = None
        if close_original:
            close_result = await self.close(old_instance_id)

        launch = await self.launch(
            kind=source.kind,
            url=target_url,
            headed=headed,
            label=source.label,
            profile=source_profile,
            stabilize=getattr(source, "stabilize", False),
            trace=getattr(source, "trace", False),
            har=bool(getattr(source, "har_path", None)),
            har_path=str(source.har_path) if getattr(source, "har_path", None) else None,
        )

        return {
            "ok": True,
            "old_instance_id": old_instance_id,
            "new_instance_id": launch["instance_id"],
            "old_closed": bool(close_result and close_result.get("closed")),
            "profile": source_profile,
            "kind": source.kind,
            "url": target_url,
            "har_path": launch.get("har_path"),
        }

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
        import shutil as _shutil

        await self.close_all()
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        # Wipe session=True tmpdirs — they only exist for this daemon's lifetime.
        # Best-effort: ignore failures (file in use, race with browser teardown).
        for tmpdir in self._session_profile_dirs.values():
            try:
                _shutil.rmtree(tmpdir, ignore_errors=True)
            except OSError:
                pass
        self._session_profile_dirs.clear()
