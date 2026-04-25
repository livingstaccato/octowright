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

# Auto-migrate legacy profile layout on first module import. Idempotent.
try:
    from . import personas as _personas

    _personas.migrate_legacy_layout()
except Exception as _e:
    log.warning("pool.migration_on_import_failed", error=repr(_e))


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


# Curated emoji pool for per-persona/per-label visual identity. 33 picks: mostly
# animals and food, no people/places. Avoids 🦊/🌐/🧭 (reserved for engines below)
# and avoids skin-tone modifiers / ZWJ sequences for cross-platform reliability.
_PERSONA_EMOJI_POOL: tuple[str, ...] = (
    # animals (15)
    "🐢",
    "🐙",
    "🐧",
    "🐼",
    "🐸",
    "🦉",
    "🐝",
    "🦋",
    "🐬",
    "🦄",
    "🐉",
    "🦔",
    "🐳",
    "🦜",
    "🐌",
    # food (10)
    "🍓",
    "🍋",
    "🍊",
    "🥑",
    "🍄",
    "🥨",
    "🍪",
    "🥐",
    "🍕",
    "🍩",
    # plants / weather (4)
    "🌵",
    "🌻",
    "🌈",
    "🌙",
    # misc (4)
    "🚀",
    "🛸",
    "🎨",
    "🔮",
)

# Engine emojis are NOT in the persona pool — they're reserved so the (persona,
# engine) pair never accidentally renders as the same glyph twice.
_ENGINE_EMOJI: dict[str, str] = {
    "chromium": "🌐",
    "firefox": "🦊",
    "webkit": "🧭",
}


def _persona_emoji_for(seed: str) -> str:
    """Stable persona emoji from a string seed. Same seed → same emoji."""
    import hashlib

    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(_PERSONA_EMOJI_POOL)
    return _PERSONA_EMOJI_POOL[idx]


def _emoji_pair_for(persona_emoji_override: str | None, name_seed: str | None, kind: str) -> str:
    """Render the ``(personEmoji engineEmoji)`` pair shown in title + badge.

    Persona emoji wins if explicitly set on the persona YAML; otherwise
    hash-pick from the curated pool keyed off the seed (persona name → label
    → short instance_id).
    """
    persona_emoji = persona_emoji_override or _persona_emoji_for(name_seed or "anon")
    engine_emoji = _ENGINE_EMOJI.get(kind, "")
    return f"({persona_emoji}{engine_emoji})"


def _title_prefix_for(
    profile: str | None,
    label: str | None,
    *,
    persona_emoji: str | None = None,
    kind: str | None = None,
) -> str | None:
    """Window-title prefix combining the emoji pair and the [tag] label.

    Without an engine kind the emoji pair is skipped — keeps backwards-compat
    for the few legacy callers that don't know the engine yet.
    """
    tag = profile or label
    if not tag:
        return None
    if kind:
        return f"{_emoji_pair_for(persona_emoji, tag, kind)} [{tag}] "
    return f"[{tag}] "


# Corner-badge injection: adds a small fixed-position label in the top-right of
# every page so 10+ parallel browsers can be told apart visually. Survives
# navigation via addInitScript + a MutationObserver re-injection guard.
_BADGE_SCRIPT = r"""
(() => {
    if (window.top !== window.self) return;
    const TAG = __TAG__;
    const COLOR = __COLOR__;
    const POS = __POS__;
    const ID = "__octowright_badge__";
    const inject = () => {
        if (!document.body) return;
        if (document.getElementById(ID)) return;
        const div = document.createElement("div");
        div.id = ID;
        div.textContent = TAG;
        const styles = {
            position: "fixed",
            zIndex: "2147483647", padding: "4px 10px",
            background: COLOR, color: "white",
            font: "bold 12px ui-monospace, Menlo, monospace",
            borderRadius: "4px", boxShadow: "0 1px 4px rgba(0,0,0,0.3)",
            textShadow: "0 0 2px rgba(0,0,0,0.7)",
            pointerEvents: "none", userSelect: "none",
        };
        styles[POS.vertical] = "8px";
        styles[POS.horizontal] = "8px";
        Object.assign(div.style, styles);
        document.body.appendChild(div);
    };
    inject();
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", inject, { once: true });
    }
    new MutationObserver(() => {
        if (document.body && !document.getElementById(ID)) inject();
    }).observe(document.documentElement || document, { childList: true, subtree: true });
})();
"""


# Badge corner positions, mapped to (vertical-css-prop, horizontal-css-prop).
# Used by _BADGE_SCRIPT to decide which two CSS edges to anchor to.
_BADGE_POSITIONS: dict[str, dict[str, str]] = {
    "top-left": {"vertical": "top", "horizontal": "left"},
    "top-right": {"vertical": "top", "horizontal": "right"},
    "bottom-left": {"vertical": "bottom", "horizontal": "left"},
    "bottom-right": {"vertical": "bottom", "horizontal": "right"},
}
_BADGE_POSITION_DEFAULT = "bottom-right"


_BADGE_ALPHA = 0.7  # translucent so page content shows through; raise for opacity


def _badge_color_for(seed: str) -> str:
    """Return a stable, translucent HSL color string for the given seed.

    Same seed always produces the same hue. Alpha is ``_BADGE_ALPHA`` so the
    page underneath remains slightly visible; the white text gets a black
    text-shadow in the JS for legibility against any backdrop.
    """
    import hashlib

    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    hue = int(digest[:6], 16) % 360
    return f"hsla({hue}, 70%, 45%, {_BADGE_ALPHA})"


def _badge_text_for(
    profile: str | None,
    label: str | None,
    instance_id: str,
    *,
    persona_emoji: str | None = None,
    kind: str | None = None,
) -> str:
    """Visible badge text. Includes the (persona engine) emoji pair when kind is set."""
    tag = profile or label or instance_id[:6]
    if kind:
        return f"{_emoji_pair_for(persona_emoji, tag, kind)} {tag}"
    return tag


def _tile_position(index: int, *, cols: int = 4, win_w: int = 720, win_h: int = 540) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) for the ``index``-th tiled window in a grid.

    Deterministic so launching the same number of browsers in the same order
    always produces the same layout — useful for muscle memory.
    """
    margin_x, margin_y = 40, 60
    gap = 20
    col = index % cols
    row = index // cols
    x = margin_x + col * (win_w + gap)
    y = margin_y + row * (win_h + gap)
    return x, y, win_w, win_h


def _tile_args_for_chromium(index: int) -> list[str]:
    """Build the Chromium CLI flags that pin a window to a tile slot.

    Firefox/WebKit don't have an equivalent CLI hook, so we no-op there and
    let the OS place those windows wherever — the badge does the heavy lifting
    for visual differentiation in those engines.
    """
    x, y, w, h = _tile_position(index)
    return [f"--window-position={x},{y}", f"--window-size={w},{h}"]


def _wire_listeners(session: BrowserSession, page: Any) -> None:
    """Attach per-page listeners (dialog, download, close, framenavigated) to a page.
    Called for both the initial page at launch AND any popup page opened mid-session.

    The close + framenavigated handlers are looked up off the session object so
    that the same hook installed at launch time can later trip eviction or log
    a user-initiated navigation respectively. Both attributes are populated by
    ``_wire_close_evictor`` (which runs immediately after the very first
    ``_wire_listeners`` call inside ``BrowserPool.launch``).
    """
    page.on("dialog", session._handle_dialog)
    page.on("download", session._handle_download)
    # If the close evictor has already attached its per-page handler, wire it
    # on this page too. For the initial launch page this is a no-op (the
    # attribute hasn't been set yet); _wire_close_evictor will install the
    # handler explicitly on the initial page right after it sets the attr.
    page_close_handler = getattr(session, "_on_page_close", None)
    if page_close_handler is not None:
        page.on("close", page_close_handler)
    framenav_handler = getattr(session, "_make_framenavigated_handler", None)
    if framenav_handler is not None:
        page.on("framenavigated", framenav_handler(page))


def _wire_close_evictor(pool: BrowserPool, session: BrowserSession) -> None:
    """When the underlying browser/context/all-pages is closed externally (OS
    close button, crash, persistent-context flush, etc.), drop the session from
    the pool registry so `pool.list_sessions()` and dashboard `/api/sessions`
    stop reporting it as live.

    Three independent signals are wired so that whichever Playwright fires
    first wins:

    1. ``session.context.on("close", _evict)`` — fires when the browser
       context closes cleanly. Persistent contexts always emit this.
    2. ``session.browser.on("disconnected", _evict)`` — fires when the
       underlying browser PROCESS dies (the OS close button on the last
       window kills the chromium process; the context-close event may not
       arrive before the connection drops). Persistent contexts have
       ``browser is None`` and skip this hook.
    3. ``page.on("close", _on_page_close)`` — installed on every page. When
       a page closes we check whether ANY page on the session is still open;
       if not, we treat that as the session being gone. Some browsers leave
       the context alive after the last page closes and wait for an idle
       timeout; we don't want to wait.

    The three signals coexist via the idempotent ``_evict`` callback —
    ``pool._sessions.pop(instance_id, None)`` returns ``None`` on the second
    and subsequent calls and the handler bails silently.

    Idempotent — safe if the session was already explicitly closed via
    ``pool.close(id)``. In the explicit-close path, ``pool.close`` removes
    the entry from ``_sessions`` BEFORE the underlying ``context.close()``
    fires its event, so the ``pop`` call below returns ``None`` and this
    handler bails silently (no double-log, no double-close on the recorder).
    """
    instance_id = session.instance_id

    def _evict(*_: Any) -> None:
        existing = pool._sessions.pop(instance_id, None)
        if existing is None:
            # Already removed by an explicit pool.close — that path logs
            # "octowright.browser.closed" itself. Stay silent.
            return
        log.info(
            "octowright.browser.evicted_externally",
            instance_id=instance_id,
            kind=session.kind,
            profile=session.profile,
            log_path=str(session.log_path),
        )
        # Best-effort: record an external-close marker in the recording so
        # post-mortem inspection shows the session ended unexpectedly. Both
        # calls may raise if the recorder was already closed by an in-flight
        # session.close() — swallow it.
        try:
            session.recorder.record("close", reason="external")
            session.recorder.close()
        except Exception:
            pass

    def _on_page_close(*_: Any) -> None:
        # Cascade to full eviction only when no page on the session is still
        # open. Single-page-of-many close (e.g. a popup being dismissed by
        # the user) is not a session death.
        try:
            still_open = [p for p in session.pages if not p.is_closed()]
        except Exception:
            still_open = []
        if not still_open:
            _evict()

    # Expose the per-page close handler so ``_wire_listeners`` can attach it
    # to the initial page AND any popup page registered later via
    # ``context.on("page", session._register_popup)``.
    session._on_page_close = _on_page_close  # type: ignore[attr-defined]

    session.context.on("close", _evict)
    # Ephemeral browsers fire 'disconnected' on the Browser when the underlying
    # process dies. Persistent contexts have no Browser handle.
    if session.browser is not None:
        session.browser.on("disconnected", _evict)


def _wire_user_navigation_logger(session: BrowserSession) -> None:
    """Publish a ``framenavigated`` handler factory on the session so that
    every page (initial + popups) can install a per-page listener via
    ``_wire_listeners``. The listener emits a ``user_navigation`` action in
    the JSONL action timeline whenever the main frame navigates — catching
    address-bar input, link clicks, and other browser-side navigations that
    the recorder would otherwise miss.

    Filters applied per-event: only main-frame navigations, never
    ``about:blank``, and de-duped against the most recent MCP-initiated
    ``BrowserSession.navigate(url)`` call (tracked via
    ``session._last_mcp_navigation``) to avoid double-logging when the user
    calls our own navigate tool.
    """

    def _make(target_page: Any) -> Any:
        def _on_framenavigated(frame: Any) -> None:
            try:
                if frame != target_page.main_frame:
                    return
                url = getattr(frame, "url", None)
                if not url or url == "about:blank":
                    return
                if getattr(session, "_last_mcp_navigation", None) == url:
                    return
                page_index: int | None
                try:
                    page_index = session.pages.index(target_page) if target_page in session.pages else None
                except ValueError:
                    page_index = None
                session.recorder.record("user_navigation", url=url, page_index=page_index)
            except Exception as e:  # pragma: no cover - defensive
                log.debug("octowright.framenavigated.swallowed", error=repr(e))

        return _on_framenavigated

    # Expose the factory so ``_wire_listeners`` can install the handler on
    # the initial page AND any later popup page.
    session._make_framenavigated_handler = _make  # type: ignore[attr-defined]


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
        badge: bool = True,
        badge_position: str = _BADGE_POSITION_DEFAULT,
        tile: bool = False,
        ephemeral: bool = False,
    ) -> dict[str, Any]:
        if kind not in SUPPORTED_KINDS:
            raise ValueError(f"kind must be one of {SUPPORTED_KINDS}, got {kind!r}")
        if badge_position not in _BADGE_POSITIONS:
            raise ValueError(f"badge_position must be one of {sorted(_BADGE_POSITIONS)}, got {badge_position!r}")

        # Promote: a named launch (label given, no explicit profile, not ephemeral)
        # gets a persistent profile by default. The whole reason for naming a
        # browser is so you can come back to it; ephemeral is the exception.
        if profile is None and label is not None and not ephemeral:
            profile = label

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

        # Chromium-only window tiling: deterministic grid based on a monotonic
        # counter incremented before any await — safe under parallel launches.
        # No-op for firefox/webkit (no equivalent CLI hook) and headless runs.
        launch_kwargs: dict[str, Any] = {}
        if tile and kind == "chromium" and not headless:
            tile_index = self._tile_counter
            self._tile_counter += 1
            launch_kwargs["args"] = _tile_args_for_chromium(tile_index)

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
                **launch_kwargs,
            )
            browser = None
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await browser_type.launch(headless=headless, **launch_kwargs)
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
        # Order matters: the close-evictor and user-nav logger publish handler
        # factories on the session so that subsequent _wire_listeners calls
        # (for popup pages) pick them up automatically. Install them BEFORE
        # the initial _wire_listeners call so the initial page also gets them.
        _wire_close_evictor(self, session)
        _wire_user_navigation_logger(session)
        _wire_listeners(session, page)
        context.on("page", session._register_popup)

        # Look up the persona's emoji override (if any) so title + badge can
        # show it. Ephemeral / unknown personas just hash-pick from the pool.
        persona_emoji_override: str | None = None
        if profile:
            try:
                from .personas import load_persona

                persona_emoji_override = load_persona(profile).emoji
            except FileNotFoundError:
                pass

        title_prefix = _title_prefix_for(profile, label, persona_emoji=persona_emoji_override, kind=kind)
        if title_prefix:
            script = _TITLE_PREFIX_SCRIPT.replace("__PREFIX__", json.dumps(title_prefix))
            await context.add_init_script(script=script)
        if badge:
            badge_text = _badge_text_for(profile, label, instance_id, persona_emoji=persona_emoji_override, kind=kind)
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
                record_video=spec.get("record_video", False),
                stabilize=spec.get("stabilize", False),
                trace=spec.get("trace", False),
                badge=spec.get("badge", True),
                badge_position=spec.get("badge_position", _BADGE_POSITION_DEFAULT),
                tile=spec.get("tile", False),
                ephemeral=spec.get("ephemeral", False),
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
        await self.close_all()
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
