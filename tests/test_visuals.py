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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
