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


def test_wire_init_scripts_substitutes_opacity(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.browser_pool.visuals as vis

    monkeypatch.setattr(vis, "_read_asset", lambda name: "const o=__OPACITY__;" if name == "badge.js" else "")
    vis._badge_script.cache_clear()

    scripts: list[str] = []

    class FakeCtx:
        async def add_init_script(self, *, script: str) -> None:
            scripts.append(script)

    import asyncio

    asyncio.get_event_loop().run_until_complete(
        vis.wire_init_scripts(
            FakeCtx(),
            profile=None,
            label="x",
            instance_id="fakeid000001",
            kind="chromium",
            badge=True,
            badge_position="bottom-right",
            stabilize=False,
        )
    )
    vis._badge_script.cache_clear()
    assert any("__OPACITY__" not in s and "0.35" in s for s in scripts)
