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

from octowright.pool import _badge_color_for, _badge_text_for


def test_badge_text_prefers_profile_over_label() -> None:
    assert _badge_text_for("disc-1", "ignored", "abcdef123456") == "disc-1"


def test_badge_text_falls_back_to_label_when_no_profile() -> None:
    assert _badge_text_for(None, "wiki", "abcdef123456") == "wiki"


def test_badge_text_falls_back_to_short_id_when_neither_set() -> None:
    """Without persona or label, show a 6-char id slice — better than blank."""
    assert _badge_text_for(None, None, "abcdef123456") == "abcdef"


def test_badge_color_is_stable_for_same_seed() -> None:
    """Same seed → same color across relaunches (the whole point — visual anchor)."""
    assert _badge_color_for("disc-1chromium") == _badge_color_for("disc-1chromium")


def test_badge_color_differs_across_seeds() -> None:
    """Different tags get different colors so 10 browsers don't all look alike."""
    colors = {_badge_color_for(f"disc-{i}chromium") for i in range(10)}
    # 10 distinct seeds — expect at least 7 distinct hues (collisions allowed
    # but not the norm). Pure-stat: hash mod 360 over 10 inputs collides rarely.
    assert len(colors) >= 7


def test_badge_color_is_well_formed_hsl() -> None:
    color = _badge_color_for("anything")
    assert color.startswith("hsl(") and color.endswith(")")
    # Sanity-check the hue parses as int in [0, 360).
    hue = int(color[len("hsl(") : color.index(",")])
    assert 0 <= hue < 360


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
        assert "probe" in text
        assert "chromium" in text

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
