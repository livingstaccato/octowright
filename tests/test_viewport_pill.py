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
        viewport_frame_inset_w=8,
        viewport_frame_inset_h=85,
    )

    scripts = [call.kwargs["script"] for call in context.add_init_script.await_args_list]
    assert any("__octowright_viewport_status__" in script for script in scripts)
    assert any('"fixed"' in script and "1280" in script and "800" in script for script in scripts)
    # The pill subtracts the same launch-measured chrome the Python side does.
    # Without it in the payload the in-page badge would have to guess, which is
    # what made it warn on every headed session.
    assert any('"inset_w": 8' in script and '"inset_h": 85' in script for script in scripts)


@pytest.mark.anyio
async def test_viewport_pill_carries_a_null_inset_when_it_was_not_measured() -> None:
    """No measurement means the pill is told so, rather than told a guess."""
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
    assert any('"inset_w": null' in script and '"inset_h": null' in script for script in scripts)


@pytest.mark.live_browser
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

        # Poll until the JS-side 1s-Alt-hold timer has fired (pill flips its
        # pointer-events to "auto"). Robust against slow CI runners where a
        # plain 1100ms wait left only ~100ms slack — flaky on macos amd64.
        await pill.evaluate(
            """async (node) => {
                const deadline = Date.now() + 4000;
                while (Date.now() < deadline) {
                    if (getComputedStyle(node).pointerEvents === 'auto') return;
                    await new Promise((r) => setTimeout(r, 50));
                }
                throw new Error('viewport pill never became interactive');
            }"""
        )
        await page.mouse.click(x, y)
        assert await page.locator("#__octowright_viewport_modal__").count() == 1
        await page.keyboard.up("Alt")
    finally:
        await pool.close_all()


@pytest.mark.live_browser
@pytest.mark.asyncio
async def test_viewport_pill_is_quiet_bottom_indicator_with_compact_popover() -> None:
    pytest.importorskip("playwright")
    from octowright.browser_pool import BrowserPool

    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body><h1>viewport</h1></body></html>",
            headed=False,
            ephemeral=True,
            label="viewport-ui",
            viewport_w=640,
            viewport_h=420,
            badge=False,
        )
        page = pool.get(result["instance_id"]).page
        pill = page.locator("#__octowright_viewport_status__")

        idle = await pill.evaluate(
            """(node) => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return {
                    text: node.textContent,
                    opacity: node.style.opacity,
                    left: node.style.left,
                    bottom: node.style.bottom,
                    top: node.style.top,
                    transform: node.style.transform,
                    boxShadow: node.style.boxShadow,
                    x: rect.x,
                    y: rect.y,
                };
            }"""
        )
        assert idle["text"] == "fixed 640x420"
        assert float(idle["opacity"]) == pytest.approx(0.18)
        assert idle["left"] == "12px"
        assert idle["bottom"] == "12px"
        assert idle["top"] == ""
        assert idle["transform"] == ""
        assert idle["boxShadow"] == "none"
        assert idle["x"] < 24
        assert idle["y"] > 360

        box = await pill.bounding_box()
        assert box is not None
        await page.keyboard.down("Alt")
        # Wait until the pill's pointer-events flip to "auto" — that's the
        # JS-side signal that the 1s Alt-hold timer fired. Using a polling
        # wait instead of a fixed sleep keeps the test reliable on slow CI
        # runners where setTimeout can lag past the historical 100ms slack.
        await pill.evaluate(
            """async (node) => {
                const deadline = Date.now() + 4000;
                while (Date.now() < deadline) {
                    if (getComputedStyle(node).pointerEvents === 'auto') return;
                    await new Promise((r) => setTimeout(r, 50));
                }
                throw new Error('viewport pill never became interactive');
            }"""
        )
        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

        popover = page.locator("#__octowright_viewport_modal__")
        await page.wait_for_selector("#__octowright_viewport_modal__", timeout=2000)
        assert await popover.count() == 1
        text = await popover.text_content()
        assert text is not None
        assert "Viewport" not in text
        assert "Sync" in text
        assert "Fluid" in text
        assert "Relaunch" not in text
        assert "Page" not in text
        assert await popover.locator("button").evaluate_all("(nodes) => nodes.map((n) => n.textContent)") == [
            "Sync",
            "Fluid",
        ]
        await page.keyboard.up("Alt")
        assert await popover.count() == 0
    finally:
        await pool.close_all()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
