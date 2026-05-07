# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import hashlib
from pathlib import Path

from provide.telemetry import get_logger

log = get_logger(__name__)


# JS init scripts live as standalone .js files for editor highlighting + lint.
# Loaded once at import — small (a few KB each) and read-only at runtime.
_ASSETS = Path(__file__).with_name("_assets")
_TITLE_TAG_SCRIPT = (_ASSETS / "title_tag.js").read_text(encoding="utf-8")
_BADGE_SCRIPT = (_ASSETS / "badge.js").read_text(encoding="utf-8")
_MACRO_STATUS_SCRIPT = (_ASSETS / "macro_pill.js").read_text(encoding="utf-8")


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
    title (e.g. ``"Yahoo (🐬🦊) [microdosing]"``). When ``kind`` is None the
    emoji pair is skipped and only ``[tag]`` is returned.
    """
    tag = profile or label
    if not tag:
        return None
    if kind:
        return f" {_emoji_pair_for(persona_emoji, tag, kind)} [{tag}]"
    return f" [{tag}]"


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


def _macro_pill_chip_for(
    profile: str | None,
    label: str | None,
    instance_id: str,
) -> tuple[str, str]:
    """(chip_text, chip_color) for the pill's left-side ID chip.

    Mirrors the corner-badge seed (``profile or label or instance_id[:6]``) so
    the same browser shows the same color in both indicators. Chip text is the
    short tag if there is one, else a 4-char id slice — keeps the pill compact.
    """
    seed = profile or label or instance_id[:6]
    chip_text = profile or label or instance_id[:4]
    return chip_text, _badge_color_for(seed)


def _describe_action(action: dict[str, object]) -> str:
    """One-line human hint for a macro action — '<verb> <key>=<value>'.

    Picks the first informative locator/value field (name → text → role → selector
    → url → key → value) so the pill stays single-line. Long values are clipped
    with an ellipsis to fit the pill's max-width.
    """
    name = str(action.get("action") or "?")
    for key in ("name", "text", "role", "selector", "url", "key", "value"):
        val = action.get(key)
        if val in (None, "", [], {}):
            continue
        s = str(val)
        if len(s) > 40:
            s = s[:39] + "…"
        return f"{name} {key}={s}"
    return name


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
