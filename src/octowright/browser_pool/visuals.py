# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import functools
import hashlib
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright.defaults import BADGE_OPACITY

log = get_logger(__name__)


# JS init scripts live as standalone .js files for editor highlighting + lint.
# Reads are deferred to first use so a wheel built without the assets still
# imports cleanly — the failure surfaces at first browser launch with a clear
# message rather than as an opaque ImportError on `import octowright`.
_ASSETS = Path(__file__).with_name("_assets")


def _read_asset(filename: str) -> str:
    path = _ASSETS / filename
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"missing octowright init-script asset {path.name!r} at {path}; broken install?"
        ) from exc


@functools.cache
def _title_tag_script() -> str:
    return _read_asset("title_tag.js")


@functools.cache
def _badge_script() -> str:
    return _read_asset("badge.js")


@functools.cache
def _macro_status_script() -> str:
    return _read_asset("macro_pill.js")


@functools.cache
def _viewport_pill_script() -> str:
    return _read_asset("viewport_pill.js")


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


# Badge positions mapped to CSS anchor properties.
# "vertical"/"horizontal" name the CSS edge to set; "v_offset"/"h_offset"
# override the default 8px inset (used for center slots); "transform" applies
# a CSS transform so center-axis slots land exactly on-center.
_BADGE_POSITIONS: dict[str, dict[str, str]] = {
    "top-left": {"vertical": "top", "horizontal": "left"},
    "top-center": {"vertical": "top", "horizontal": "left", "transform": "translateX(-50%)", "h_offset": "50%"},
    "top-right": {"vertical": "top", "horizontal": "right"},
    "left-center": {"vertical": "top", "horizontal": "left", "transform": "translateY(-50%)", "v_offset": "50%"},
    "right-center": {"vertical": "top", "horizontal": "right", "transform": "translateY(-50%)", "v_offset": "50%"},
    "bottom-left": {"vertical": "bottom", "horizontal": "left"},
    "bottom-center": {"vertical": "bottom", "horizontal": "left", "transform": "translateX(-50%)", "h_offset": "50%"},
    "bottom-right": {"vertical": "bottom", "horizontal": "right"},
}
_BADGE_POSITION_DEFAULT = "bottom-right"


_BADGE_ALPHA = 0.45  # translucent so page content shows through; adjust for opacity


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


# `_describe_action` used to live here; it was moved to
# `octowright.macros.descriptions` so the macro layer doesn't have to
# import from the pool layer (wrong direction). The pool layer still
# needs the helper for pill / badge rendering, so we re-export under the
# original underscore name to keep existing call sites unchanged.
#
# The re-export goes through a thin wrapper rather than a top-level
# ``from … import describe_action`` to avoid triggering the
# ``octowright.macros`` package ``__init__`` (which pulls in
# ``execution`` → ``repair`` → ``macros.semantic``)
# during module import — visuals is loaded by ``browser_pool.options``,
# which itself imports during ``server._state`` initialisation, creating
# a cycle.


def _describe_action(action: dict[str, Any]) -> str:
    """Re-export of :func:`octowright.macros.descriptions.describe_action`."""
    from octowright.macros.descriptions import describe_action

    return describe_action(action)


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


# --- per-context init-script wiring ----------------------------------------
#
# Pulled out of BrowserPool.launch so launch stays below the complexity gate.
# Adds title-tag, corner badge, macro pill, and stabilize shim init scripts
# to a Playwright BrowserContext.


def _resolve_persona_emoji(profile: str | None) -> str | None:
    """Look up the persona's emoji override. Ephemeral / unknown personas
    fall through to the hash-pick path downstream."""
    if not profile:
        return None
    try:
        from octowright.personas import load_persona

        return load_persona(profile).emoji
    except FileNotFoundError:
        return None


async def wire_init_scripts(
    context: Any,
    *,
    profile: str | None,
    label: str | None,
    instance_id: str,
    kind: str,
    badge: bool,
    badge_position: str,
    stabilize: bool,
    viewport_mode: str = "unknown",
    viewport_width: int | None = None,
    viewport_height: int | None = None,
) -> None:
    """Inject title-tag, badge, macro-pill, and (optional) stabilize scripts."""
    import json as _json

    from octowright.stabilize import render_stabilize_script

    persona_emoji = _resolve_persona_emoji(profile)

    title_tag = _title_tag_for(profile, label, persona_emoji=persona_emoji, kind=kind)
    if title_tag:
        script = _title_tag_script().replace("__SUFFIX__", _json.dumps(title_tag))
        await context.add_init_script(script=script)

    if badge:
        badge_text = _badge_text_for(profile, label, instance_id, persona_emoji=persona_emoji, kind=kind)
        # Color seed is persona-stable: chromium / firefox / webkit launches
        # of the same persona share one color. Engine emoji handles engine
        # differentiation.
        color_seed = profile or label or instance_id[:6]
        badge_script = (
            _badge_script()
            .replace("__TAG__", _json.dumps(badge_text))
            .replace("__COLOR__", _json.dumps(_badge_color_for(color_seed)))
            .replace("__POS__", _json.dumps(_BADGE_POSITIONS[badge_position]))
            .replace("__OPACITY__", _json.dumps(BADGE_OPACITY))
        )
        await context.add_init_script(script=badge_script)

    # Macro status pill — overlay stays invisible until a running macro
    # pushes text via window.__octowright_macro_status.
    chip_text, chip_color = _macro_pill_chip_for(profile, label, instance_id)
    pill_script = (
        _macro_status_script()
        .replace("__ID_TAG__", _json.dumps(chip_text))
        .replace("__ID_COLOR__", _json.dumps(chip_color))
    )
    await context.add_init_script(script=pill_script)

    viewport_payload = {
        "mode": viewport_mode,
        "width": viewport_width,
        "height": viewport_height,
    }
    viewport_script = _viewport_pill_script().replace("__VIEWPORT_INFO__", _json.dumps(viewport_payload))
    await context.add_init_script(script=viewport_script)

    if stabilize:
        await context.add_init_script(script=render_stabilize_script())
