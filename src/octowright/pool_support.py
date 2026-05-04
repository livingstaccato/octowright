# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from .session import BrowserSession

if TYPE_CHECKING:
    from .pool import BrowserPool

log = get_logger(__name__)

_TITLE_TAG_SCRIPT = r"""
(() => {
    const SUFFIX = __SUFFIX__;
    // SUFFIX is " (emoji) [tag]" with a leading space. Browsers strip trailing
    // whitespace from titles on read, but a leading space inside the actual
    // value survives. We compare on a trimmed anchor so any double-injection
    // (e.g. "Yahoo (🐬🦊) [acct] (🐬🦊) [acct]") collapses back to a single tag.
    const SUFFIX_BASE = SUFFIX.replace(/^\s+/, "");
    const ensure = (v) => {
        const s = String(v == null ? "" : v);
        return s.endsWith(SUFFIX_BASE) ? s : s + SUFFIX;
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


def _title_tag_for(
    profile: str | None,
    label: str | None,
    *,
    persona_emoji: str | None = None,
    kind: str | None = None,
) -> str | None:
    """Window-title suffix combining the emoji pair and the [tag] label.

    Returned with a leading space so it appends cleanly after the page's own
    title (e.g. ``"Yahoo (🐬🦊) [microdosing]"``). Without an engine kind the
    emoji pair is skipped — keeps backwards-compat for the few legacy callers
    that don't know the engine yet.
    """
    tag = profile or label
    if not tag:
        return None
    if kind:
        return f" {_emoji_pair_for(persona_emoji, tag, kind)} [{tag}]"
    return f" [{tag}]"


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
    page.on("response", session._handle_response)
    page.on("requestfailed", session._handle_request_failed)
    page.on("websocket", session._handle_websocket)
    page.on("load", lambda: session._schedule_markdown_capture(page=page, force=True))
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
                # Capture a markdown snapshot for the new destination so cache
                # stays aligned with navigation history (for the currently
                # new destination.
                session._schedule_markdown_capture(page=target_page)
            except Exception as e:  # pragma: no cover - defensive
                log.debug("octowright.framenavigated.swallowed", error=repr(e))

        return _on_framenavigated

    # Expose the factory so ``_wire_listeners`` can install the handler on
    # the initial page AND any later popup page.
    session._make_framenavigated_handler = _make  # type: ignore[attr-defined]
