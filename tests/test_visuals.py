# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Visuals module — badge opacity configuration tests."""

from __future__ import annotations

import pytest


def test_badge_script_uses_opacity_template_variable() -> None:
    from octowright.browser_pool.visuals import _badge_script

    assert "__OPACITY__" in _badge_script()


@pytest.mark.anyio
async def test_wire_init_scripts_substitutes_opacity(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.browser_pool.visuals as vis

    # Pre-warm all non-badge caches so the monkeypatched _read_asset (which
    # returns "" for non-badge.js names) cannot poison them.
    vis._title_tag_script()
    vis._macro_status_script()
    vis._viewport_pill_script()

    monkeypatch.setattr(vis, "_read_asset", lambda name: "const o=__OPACITY__;" if name == "badge.js" else "")
    vis._badge_script.cache_clear()

    scripts: list[str] = []

    class FakeCtx:
        async def add_init_script(self, *, script: str) -> None:
            scripts.append(script)

    await vis.wire_init_scripts(
        FakeCtx(),
        profile=None,
        label="x",
        instance_id="fakeid000001",
        kind="chromium",
        badge=True,
        badge_position="bottom-right",
        stabilize=False,
    )
    vis._badge_script.cache_clear()
    assert any("__OPACITY__" not in s and "0.35" in s for s in scripts)


def test_all_eight_positions_exist() -> None:
    from octowright.browser_pool.visuals import _BADGE_POSITIONS

    expected = {
        "top-left",
        "top-center",
        "top-right",
        "left-center",
        "right-center",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    }
    assert expected == set(_BADGE_POSITIONS.keys())


def test_center_positions_have_transform() -> None:
    from octowright.browser_pool.visuals import _BADGE_POSITIONS

    for key in ("top-center", "bottom-center"):
        assert "transform" in _BADGE_POSITIONS[key]
        assert "translateX(-50%)" in _BADGE_POSITIONS[key]["transform"]
    for key in ("left-center", "right-center"):
        assert "transform" in _BADGE_POSITIONS[key]
        assert "translateY(-50%)" in _BADGE_POSITIONS[key]["transform"]


def test_badge_script_has_popup_template_vars() -> None:
    from octowright.browser_pool.visuals import _badge_script

    src = _badge_script()
    assert "__DASHBOARD_URL__" in src
    assert "__INSTANCE_ID__" in src
    assert "altKey" in src


@pytest.mark.anyio
async def test_wire_init_scripts_substitutes_dashboard_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.browser_pool.visuals as vis
    import octowright.defaults as defs

    # Pre-warm all non-badge caches so the monkeypatched _read_asset (which
    # returns "" for non-badge.js names) cannot poison them.
    vis._title_tag_script()
    vis._macro_status_script()
    vis._viewport_pill_script()

    monkeypatch.setattr(
        vis,
        "_read_asset",
        lambda name: "const d=__DASHBOARD_URL__;const i=__INSTANCE_ID__;" if name == "badge.js" else "",
    )
    vis._badge_script.cache_clear()
    monkeypatch.setattr(defs, "get_default_url", lambda: "http://127.0.0.1:6286/new-tab")

    scripts: list[str] = []

    class FakeCtx:
        async def add_init_script(self, *, script: str) -> None:
            scripts.append(script)

    await vis.wire_init_scripts(
        FakeCtx(),
        profile=None,
        label="x",
        instance_id="fakeid000001",
        kind="chromium",
        badge=True,
        badge_position="bottom-right",
        stabilize=False,
    )
    vis._badge_script.cache_clear()
    badge_script = next(s for s in scripts if "fakeid000001" in s or "6286" in s)
    assert "http://127.0.0.1:6286" in badge_script
    assert "fakeid000001" in badge_script


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
