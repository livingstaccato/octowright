# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Record a local page with optional live feedback: uv run python examples/interaction-indicators.py."""

from __future__ import annotations

import asyncio

from octowright.browser_pool import BrowserPool

PAGE = """<style>body{height:2000px}button{margin:80px;padding:50px}</style>
<button id="target">Try clicking, holding, dragging and scrolling</button>"""


async def main() -> None:
    pool = BrowserPool()
    try:
        launched = await pool.launch(
            kind="chromium",
            url="about:blank",
            headed=False,
            ephemeral=True,
            record_video=True,
            show_interactions=True,
            record_interactions=True,
        )
        session = pool.get(launched["instance_id"])
        await session.context.route(
            "http://interaction-example.test/**",
            lambda route: route.fulfill(body=PAGE, content_type="text/html"),
        )
        await session.navigate("http://interaction-example.test/")
        await session.click("#target")
        # Raw native input models manual input; the same observer sees both paths.
        await session.page.mouse.click(160, 120, button="right")
        await session.page.mouse.move(160, 120)
        await session.page.mouse.down()
        # Deliberate demonstration hold, not a test synchronization assertion.
        await session.page.wait_for_timeout(750)
        await session.page.mouse.move(260, 180, steps=8)
        await session.page.mouse.up()
        await session.scroll(delta_x=0, delta_y=160)
        await session.set_interaction_visuals(show=False)  # metadata-only from here
        await session.click("#target")
        print(f"Action log: {session.log_path}")
        print(f"Interaction metadata: {session.log_path.with_suffix('.interactions.jsonl')}")
        print(f"Native video: {await session.page.video.path()}")
        await pool.close(session.instance_id, force=True)
    finally:
        await pool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
