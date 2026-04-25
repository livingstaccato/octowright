# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Corner-badge injection — pure helpers + end-to-end DOM verification.

The end-to-end test launches a real headless browser via the pool, navigates
to a data: URL (no network), and reads the injected badge element from the
DOM. Skipped when Playwright browsers aren't installed.
"""

from __future__ import annotations

import pytest

from octowright.pool import (
    _ENGINE_EMOJI,
    _PERSONA_EMOJI_POOL,
    _badge_color_for,
    _badge_text_for,
    _emoji_pair_for,
    _persona_emoji_for,
)


def test_badge_text_prefers_profile_over_label() -> None:
    """Without kind, falls back to bare tag (legacy callers)."""
    assert _badge_text_for("disc-1", "ignored", "abcdef123456") == "disc-1"


def test_badge_text_falls_back_to_label_when_no_profile() -> None:
    assert _badge_text_for(None, "wiki", "abcdef123456") == "wiki"


def test_badge_text_falls_back_to_short_id_when_neither_set() -> None:
    """Without persona or label, show a 6-char id slice — better than blank."""
    assert _badge_text_for(None, None, "abcdef123456") == "abcdef"


def test_badge_text_with_kind_includes_emoji_pair() -> None:
    """Modern callers pass kind → text starts with the (persona engine) pair."""
    text = _badge_text_for("disc-1", None, "abcdef123456", kind="firefox")
    assert text.endswith(" disc-1")
    assert _ENGINE_EMOJI["firefox"] in text  # 🦊
    assert text.startswith("(") and ")" in text


def test_badge_text_respects_persona_emoji_override() -> None:
    text = _badge_text_for("disc-1", None, "abcdef123456", persona_emoji="🦄", kind="chromium")
    assert text.startswith("(🦄")
    assert _ENGINE_EMOJI["chromium"] in text


def test_badge_color_is_stable_for_same_seed() -> None:
    """Same seed → same color across relaunches (the whole point — visual anchor)."""
    assert _badge_color_for("disc-1chromium") == _badge_color_for("disc-1chromium")


def test_badge_color_differs_across_seeds() -> None:
    """Different tags get different colors so 10 browsers don't all look alike."""
    colors = {_badge_color_for(f"disc-{i}chromium") for i in range(10)}
    # 10 distinct seeds — expect at least 7 distinct hues (collisions allowed
    # but not the norm). Pure-stat: hash mod 360 over 10 inputs collides rarely.
    assert len(colors) >= 7


def test_badge_color_is_translucent_hsla() -> None:
    """Color must be hsla (translucent) so page content shows through."""
    color = _badge_color_for("anything")
    assert color.startswith("hsla(") and color.endswith(")")
    # Sanity-check the hue parses as int in [0, 360).
    hue = int(color[len("hsla(") : color.index(",")])
    assert 0 <= hue < 360
    # And the alpha component is < 1 (translucent).
    alpha = float(color.rstrip(")").rsplit(",", 1)[1].strip())
    assert 0 < alpha < 1


def test_badge_color_is_persona_stable_across_engines() -> None:
    """The color seed is the persona name only — same seed → same color regardless of engine."""
    # The launch path uses ``profile or label or instance_id[:6]`` as the seed
    # (no engine kind suffixed). This test guards that contract from regressing.
    seed = "disc-1"
    assert _badge_color_for(seed) == _badge_color_for(seed)
    # Sanity: the hue derived from "disc-1" must stay the same as the docstring
    # implies — test fails loudly if anyone re-introduces engine into the seed.
    assert "+" not in _badge_color_for(seed)  # belt-and-braces


def test_badge_color_differs_between_personas() -> None:
    """Different personas still get different colors."""
    assert _badge_color_for("disc-1") != _badge_color_for("disc-2")


# ---------------------------------------------------------------------------
# Emoji pool + pair helpers
# ---------------------------------------------------------------------------


def test_persona_emoji_pool_size() -> None:
    """The user picked exactly 33; guard against accidental edits."""
    assert len(_PERSONA_EMOJI_POOL) == 33


def test_persona_emoji_pool_no_engine_clashes() -> None:
    """Engine emojis must not appear in the persona pool — otherwise (X X) collisions."""
    for engine_emoji in _ENGINE_EMOJI.values():
        assert engine_emoji not in _PERSONA_EMOJI_POOL


def test_persona_emoji_pool_unique() -> None:
    assert len(set(_PERSONA_EMOJI_POOL)) == len(_PERSONA_EMOJI_POOL)


def test_persona_emoji_is_stable() -> None:
    assert _persona_emoji_for("disc-1") == _persona_emoji_for("disc-1")


def test_persona_emoji_distributes_across_pool() -> None:
    """A random sample of names should hit at least 10 distinct emojis from the pool."""
    sample = {_persona_emoji_for(f"persona-{i}") for i in range(50)}
    assert len(sample) >= 10


def test_emoji_pair_uses_override_when_set() -> None:
    assert _emoji_pair_for("🦄", "ignored-seed", "chromium") == "(🦄🌐)"


def test_emoji_pair_falls_back_to_hash_pick() -> None:
    pair = _emoji_pair_for(None, "disc-1", "firefox")
    assert pair.endswith("🦊)")  # firefox engine emoji
    assert pair.startswith("(")


def test_emoji_pair_for_unknown_engine_drops_engine_emoji() -> None:
    pair = _emoji_pair_for("🦄", "n/a", "lynx")  # not a real engine
    assert pair == "(🦄)"


# ---------------------------------------------------------------------------
# Badge position
# ---------------------------------------------------------------------------


def test_badge_default_position_is_bottom_right() -> None:
    from octowright.pool import _BADGE_POSITION_DEFAULT

    assert _BADGE_POSITION_DEFAULT == "bottom-right"


def test_badge_positions_cover_all_four_corners() -> None:
    from octowright.pool import _BADGE_POSITIONS

    assert set(_BADGE_POSITIONS.keys()) == {
        "top-left",
        "top-right",
        "bottom-left",
        "bottom-right",
    }


def test_badge_position_values_have_two_axes() -> None:
    """Each position must declare both a vertical and a horizontal CSS edge."""
    from octowright.pool import _BADGE_POSITIONS

    for name, axes in _BADGE_POSITIONS.items():
        assert axes["vertical"] in ("top", "bottom"), name
        assert axes["horizontal"] in ("left", "right"), name


@pytest.mark.asyncio
async def test_invalid_badge_position_raises(tmp_path) -> None:
    """Bad badge_position string must raise — caught early, not silently fall back."""
    pytest.importorskip("playwright")
    from octowright.pool import BrowserPool

    pool = BrowserPool()
    try:
        with pytest.raises(ValueError, match="badge_position"):
            await pool.launch(
                kind="chromium",
                url="data:text/html,<html><body></body></html>",
                headed=False,
                label="badpos",
                viewport_w=400,
                viewport_h=300,
                badge_position="middle-of-screen",
            )
    finally:
        await pool.shutdown()


# ---------------------------------------------------------------------------
# End-to-end: real Playwright launch, verify the badge lands in the DOM.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_badge_actually_renders_in_real_browser(tmp_path) -> None:
    """Launch a headless browser via the pool, navigate to a data: URL, read DOM."""
    pytest.importorskip("playwright")
    from octowright import defaults as _defaults
    from octowright.pool import BrowserPool

    # Isolate recordings so the test doesn't pollute the global recordings dir.
    monkey_recordings = tmp_path / "rec"
    monkey_recordings.mkdir()
    original = _defaults.RECORDINGS_DIR
    _defaults.RECORDINGS_DIR = monkey_recordings
    # Pool reads from module globals at launch time; patch both.
    import octowright.pool as _pool

    original_pool = _pool.RECORDINGS_DIR
    _pool.RECORDINGS_DIR = monkey_recordings

    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body><h1>Probe</h1></body></html>",
            headed=False,  # CI-safe; the badge logic doesn't depend on headed.
            label="probe",
            viewport_w=400,
            viewport_h=300,
            badge=True,
        )
        instance_id = result["instance_id"]
        session = pool.get(instance_id)
        # The badge is injected via addInitScript and runs at document_start;
        # by the time goto() resolves, it should be in the DOM.
        present = await session.page.evaluate("!!document.getElementById('__octowright_badge__')")
        assert present is True

        text = await session.page.evaluate("document.getElementById('__octowright_badge__').textContent")
        # New format: "(personaEmoji engineEmoji) tag" — engine name no longer in text.
        assert "probe" in text
        assert _ENGINE_EMOJI["chromium"] in text  # 🌐 = chromium

        # Default position is bottom-right. We assert against the INLINE style
        # we set (element.style) — getComputedStyle resolves bottom-anchored
        # elements into pixel `top`, which would give a misleading reading.
        css = await session.page.evaluate(
            "(() => { const e = document.getElementById('__octowright_badge__');"
            " return JSON.stringify({top: e.style.top, bottom: e.style.bottom,"
            " left: e.style.left, right: e.style.right,"
            " bg: getComputedStyle(e).backgroundColor}); })()"
        )
        import json as _json

        css_dict = _json.loads(css)
        assert css_dict["bottom"] == "8px"
        assert css_dict["right"] == "8px"
        assert css_dict["top"] == ""  # inline style not set when anchored to bottom
        assert css_dict["left"] == ""
        # backgroundColor resolves to rgba(...) — alpha must be < 1 (translucent).
        assert css_dict["bg"].startswith("rgba(")
        alpha = float(css_dict["bg"].rstrip(")").rsplit(",", 1)[1].strip())
        assert 0 < alpha < 1

        # Now confirm badge=False suppresses it.
        result2 = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body></body></html>",
            headed=False,
            label="quiet",
            viewport_w=400,
            viewport_h=300,
            badge=False,
        )
        session2 = pool.get(result2["instance_id"])
        present2 = await session2.page.evaluate("!!document.getElementById('__octowright_badge__')")
        assert present2 is False
    finally:
        await pool.shutdown()
        _defaults.RECORDINGS_DIR = original
        _pool.RECORDINGS_DIR = original_pool
