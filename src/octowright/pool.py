from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from playwright.async_api import Playwright, async_playwright
from provide.telemetry import get_logger

from .defaults import (
    DEFAULT_URL,
    DEFAULT_VIEWPORT_H,
    DEFAULT_VIEWPORT_W,
    HEADLESS_DEFAULT,
    RECORDINGS_DIR,
    SUPPORTED_KINDS,
)
from .profiles import profile_dir
from .recorder import Recorder, new_log_path
from .session import BrowserSession
from .stabilize import render_stabilize_script

log = get_logger(__name__)


_TITLE_PREFIX_SCRIPT = r"""
(() => {
    const PREFIX = __PREFIX__;
    const ensure = (v) => {
        const s = String(v == null ? "" : v);
        return s.startsWith(PREFIX) ? s : PREFIX + s;
    };
    const desc = Object.getOwnPropertyDescriptor(Document.prototype, "title");
    if (desc && desc.get && desc.set) {
        Object.defineProperty(Document.prototype, "title", {
            configurable: true,
            enumerable: desc.enumerable,
            get() { return desc.get.call(this); },
            set(v) { desc.set.call(this, ensure(v)); },
        });
    }
    const apply = () => {
        try {
            const cur = document.title || "";
            const want = ensure(cur);
            if (cur !== want) document.title = want;
        } catch (_) {}
    };
    apply();
    const watchHead = () => {
        const head = document.querySelector("head");
        if (!head) return false;
        new MutationObserver(apply).observe(head, {
            subtree: true, childList: true, characterData: true,
        });
        return true;
    };
    const onReady = () => { watchHead(); apply(); };
    if (!watchHead()) {
        document.addEventListener("DOMContentLoaded", onReady, { once: true });
    }
    window.addEventListener("load", apply, { once: true });
})();
"""


def _title_prefix_for(profile: str | None, label: str | None) -> str | None:
    tag = profile or label
    return f"[{tag}] " if tag else None


class BrowserPool:
    """Owns a single Playwright driver and a dict of active BrowserSession objects.

    One playwright instance is shared across all sessions; each session gets its own
    Browser, BrowserContext, and Page.
    """

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._sessions: dict[str, BrowserSession] = {}

    async def _ensure_pw(self) -> Playwright:
        if self._pw is None:
            self._pw = await async_playwright().start()
        return self._pw

    async def launch(
        self,
        *,
        kind: str,
        url: str | None,
        headed: bool,
        label: str | None,
        viewport_w: int | None,
        viewport_h: int | None,
        profile: str | None = None,
        stabilize: bool = False,
        record_video: bool = False,
        trace: bool = False,
    ) -> dict[str, Any]:
        if kind not in SUPPORTED_KINDS:
            raise ValueError(f"kind must be one of {SUPPORTED_KINDS}, got {kind!r}")

        pw = await self._ensure_pw()
        browser_type = getattr(pw, kind)
        headless = not headed if headed is not None else HEADLESS_DEFAULT

        vw = viewport_w or DEFAULT_VIEWPORT_W
        vh = viewport_h or DEFAULT_VIEWPORT_H

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

        if profile:
            pdir = profile_dir(kind, profile)
            pdir.mkdir(parents=True, exist_ok=True)
            user_data_dir = str(pdir)
            context = await browser_type.launch_persistent_context(
                user_data_dir,
                headless=headless,
                viewport={"width": vw, "height": vh},
                accept_downloads=True,
                **ctx_video_kwargs,
            )
            browser = None
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await browser_type.launch(headless=headless)
            context = await browser.new_context(
                viewport={"width": vw, "height": vh},
                accept_downloads=True,
                **ctx_video_kwargs,
            )
            page = await context.new_page()

        target_url = url or DEFAULT_URL
        instance_id = uuid.uuid4().hex[:12]

        log_path = new_log_path(RECORDINGS_DIR, instance_id, label, kind)
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
            viewport={"w": vw, "h": vh},
            stabilize=stabilize,
            record_video=record_video,
            video_dir=str(video_dir) if video_dir else None,
            trace=trace,
        )

        session = BrowserSession(
            instance_id=instance_id,
            kind=kind,
            label=label,
            url=target_url,
            browser=browser,
            context=context,
            page=page,
            recorder=recorder,
            log_path=log_path,
            profile=profile,
            stabilize=stabilize,
            trace=trace,
        )
        # Wire up video tracking — page.video is only non-None when record_video_dir was set.
        if record_video and page.video is not None:
            session._video = page.video
        session.attach_console()
        page.on("dialog", session._handle_dialog)
        page.on("download", session._handle_download)
        context.on("page", session._register_popup)

        title_prefix = _title_prefix_for(profile, label)
        if title_prefix:
            script = _TITLE_PREFIX_SCRIPT.replace("__PREFIX__", json.dumps(title_prefix))
            await context.add_init_script(script=script)
        if stabilize:
            await context.add_init_script(script=render_stabilize_script())

        if trace:
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        await page.goto(target_url)

        self._sessions[instance_id] = session
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
        }
        if video_dir is not None:
            result["video_dir"] = str(video_dir)
        return result

    def get(self, instance_id: str) -> BrowserSession:
        if instance_id not in self._sessions:
            raise KeyError(f"no browser with instance_id={instance_id!r}; known: {list(self._sessions)}")
        return self._sessions[instance_id]

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "instance_id": s.instance_id,
                "kind": s.kind,
                "label": s.label,
                "profile": s.profile,
                "url": s.url,
                "log_path": str(s.log_path),
            }
            for s in self._sessions.values()
        ]

    def profile_in_use(self, kind: str, profile: str) -> bool:
        return any(s.kind == kind and s.profile == profile for s in self._sessions.values())

    async def close(self, instance_id: str) -> dict[str, Any]:
        session = self.get(instance_id)
        await session.close()
        del self._sessions[instance_id]
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
        }

    async def close_all(self) -> dict[str, Any]:
        ids = list(self._sessions.keys())
        for iid in ids:
            await self.close(iid)
        return {"closed": ids}

    async def spawn_roster(self, specs: list[dict[str, Any]]) -> dict[str, Any]:
        """Launch N browsers concurrently from a list of launch spec dicts.

        Each spec may contain any subset of: kind, url, headed, label, profile,
        viewport_w, viewport_h, stabilize, record_video.  Runs with
        asyncio.gather so they boot in parallel.  An error on one browser does
        NOT abort the others.

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
                stabilize=spec.get("stabilize", False),
                record_video=spec.get("record_video", False),
            )

        results = await asyncio.gather(*[_launch_one(s) for s in specs], return_exceptions=True)

        launched: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for spec, result in zip(specs, results):
            if isinstance(result, BaseException):
                errors.append({"spec": spec, "error": str(result)})
            else:
                launched.append(result)
        return {"launched": launched, "errors": errors}

    async def shutdown(self) -> None:
        await self.close_all()
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
