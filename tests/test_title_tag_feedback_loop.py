# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The title tag must not fight a page over its own title.

``title_tag.js`` overrides ``Document.prototype.title``'s *setter* and also
observes the ``<head>`` subtree its own write mutates. Its ``cur !== want``
guard only stops it re-entering itself; it cannot stop a **two-party** loop
with a page that re-asserts its own title after every change. Against such a
page the two ping-pong forever: the renderer's main thread pegs (``evaluate``
stops answering) and RSS climbs about 1GB/s.

Measured on a Cloudflare challenge page 2026-09-11: a single Firefox content
process reached ~20GB in 140s, which OOM-killed every sibling browser in the
pool and then the daemon's own sessions. Raw Playwright on the identical page
with no init scripts stayed flat at 0.54GB, and injecting this one script
reproduced the climb to 9.33GB in 10s — so the page was never the cause.

The reproducer below is hermetic: the adversary is three lines of page script
re-asserting a fixed title, which is exactly what a status-updating page does.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import pytest

# A page that restores its own title whenever anything changes it.
ADVERSARY_HTML = """<html><head><title>Fixed</title></head><body>
<script>
  new MutationObserver(function () {
    if (document.title !== 'Fixed') { document.title = 'Fixed'; }
  }).observe(document.querySelector('head'),
             {subtree: true, childList: true, characterData: true});
</script>
</body></html>"""


@pytest.mark.live_browser
@pytest.mark.asyncio
async def test_title_tag_does_not_wedge_a_page_that_reasserts_its_title() -> None:
    pytest.importorskip("playwright")
    from octowright.browser_pool import BrowserPool

    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html," + quote(ADVERSARY_HTML),
            headed=False,
            ephemeral=True,
            label="titleloop",
            badge=False,
        )
        page = pool.get(result["instance_id"]).page

        # Give the two parties time to reach a fixed point (or fail to).
        await asyncio.sleep(2)

        # The load-bearing assertion: the renderer still answers. Under the
        # feedback loop the main thread is saturated and this times out — the
        # exact symptom seen live, where even a probe evaluate could not run.
        answered = await asyncio.wait_for(page.evaluate("1 + 1"), timeout=5)
        assert answered == 2

        # And the feature still works against exactly that page. Backing off is
        # only the right fix if the tag survives it; a version that simply gave
        # up and never tagged a contested page would satisfy the check above,
        # which is why this one is here. The tag is re-applied on a timer, so
        # poll rather than sampling once — the assertion is that it comes back,
        # not that it is present at every instant.
        # Read the <title> NODE, not document.title: the node is what the window
        # and tab chrome actually render, and page-side reads are deliberately
        # handed back the page's own untagged value (that masking is what stops
        # the page reverting anything, so asserting on it would be asserting on
        # the wrong half of the design).
        rendered = await page.evaluate(
            "() => { const t = document.querySelector('title'); return t ? t.textContent : ''; }"
        )
        assert "titleloop" in rendered, f"window title lost its tag on a contested page: {rendered!r}"
    finally:
        await pool.close_all()
