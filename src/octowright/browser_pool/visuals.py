# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from provide.telemetry import get_logger

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

    digest = hashlib.sha1(seed.encode("utf-8"), usedforsecurity=False).hexdigest()
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

    digest = hashlib.sha1(seed.encode("utf-8"), usedforsecurity=False).hexdigest()
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
