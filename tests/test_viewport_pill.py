# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from unittest.mock import AsyncMock

import pytest

from octowright.browser_pool.visuals import wire_init_scripts


@pytest.mark.anyio
async def test_viewport_pill_script_is_injected() -> None:
    context = type("Context", (), {"add_init_script": AsyncMock()})()

    await wire_init_scripts(
        context,
        profile=None,
        label="player",
        instance_id="abc123",
        kind="chromium",
        badge=False,
        badge_position="bottom-right",
        stabilize=False,
        viewport_mode="fixed",
        viewport_width=1280,
        viewport_height=800,
    )

    scripts = [call.kwargs["script"] for call in context.add_init_script.await_args_list]
    assert any("__octowright_viewport_status__" in script for script in scripts)
    assert any('"fixed"' in script and "1280" in script and "800" in script for script in scripts)


@pytest.mark.asyncio
async def test_viewport_pill_requires_one_second_alt_hold() -> None:
    pytest.importorskip("playwright")
    from octowright.browser_pool import BrowserPool

    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body><h1>viewport</h1></body></html>",
            headed=False,
            ephemeral=True,
            label="viewport-alt",
            viewport_w=640,
            viewport_h=420,
            badge=False,
        )
        page = pool.get(result["instance_id"]).page
        pill = page.locator("#__octowright_viewport_status__")
        box = await pill.bounding_box()
        assert box is not None
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2

        await page.keyboard.down("Alt")
        await page.mouse.click(x, y)
        assert await page.locator("#__octowright_viewport_modal__").count() == 0

        await page.wait_for_timeout(1100)
        await page.mouse.click(x, y)
        assert await page.locator("#__octowright_viewport_modal__").count() == 1
        await page.keyboard.up("Alt")
    finally:
        await pool.close_all()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
