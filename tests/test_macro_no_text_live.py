# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``expect_no_text`` means *text a reader can see*, on every engine alike.

Each page here was a measured disagreement: an engine, or Chromium's DOM
snapshot, counting something nobody can read (a URL, a hidden subtree's source)
or missing something everyone can (a ``display: contents`` wrapper once hid the
whole app on Firefox and WebKit). Served over loopback HTTP because ``file://``
is refused by navigation.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool
from octowright.session.rendered_text import ELEMENT_LIMIT

pytestmark = pytest.mark.live_browser

PAGES: dict[str, bytes] = {
    # A: a wrapper the layout skips (display: contents) still draws its children.
    "/contents": b"""<!doctype html><body><div style="display:contents"><p>TOKEN-CONTENTS</p></div>
<div style="display:contents"><p style="visibility:hidden">TOKEN-CONTENTS-HIDDEN</p></div></body>""",
    # B: nothing inside an element that is not rendered is drawn.
    "/unrendered": b"""<!doctype html><body>
<div class="t" style="display:none">TOKEN-MATCH-HIDDEN</div>
<x-card id="card" style="display:none"></x-card><div id="styled"></div>
<iframe style="display:none" srcdoc="<p>TOKEN-HIDDEN-FRAME</p>"></iframe>
<script>
document.getElementById("card").attachShadow({mode: "open"}).innerHTML = "<p>TOKEN-HIDDEN-HOST</p>";
document.getElementById("styled").attachShadow({mode: "open"}).innerHTML =
  "<style>.TOKEN-SHADOW-CSS { color: red }</style><p>shown</p>";
</script></body>""",
    # D: attribute and address text is not drawn; alt text of a broken image and
    # a select's option labels are, on every engine.
    "/attributes": b"""<!doctype html><body>
<img src="/img?t=TOKEN-IMG-SRC" alt="TOKEN-ALT-BROKEN"><p data-token="TOKEN-DATA-ATTR">phone 555-013-7788</p>
<div style="width:10px;height:10px;background-image:url(/bg?t=TOKEN-STYLE-IMAGE)"></div>
<input value="filled" placeholder="TOKEN-PLACEHOLDER-UNDER-VALUE">
<select><option>TOKEN-OPTION-A</option><option selected>other</option></select>
<a href="/go?t=TOKEN-LINK-HREF" title="TOKEN-TITLE">link</a></body>""",
    "/closed": b"""<!doctype html><body><div id="closed-host"></div><script>
document.getElementById("closed-host").attachShadow({mode: "closed"}).innerHTML = "<span>TOKEN-CLOSED</span>";
</script></body>""",
    "/huge": b"<!doctype html><body><div id=all></div><script>"
    b"document.getElementById('all').innerHTML = '<i></i>'.repeat(" + str(ELEMENT_LIMIT + 10).encode() + b");"
    b"</script><p id=small>just a paragraph</p></body>",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = PAGES.get(self.path.split("?")[0])
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        payload = body or b""
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def base_url() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def session(request: pytest.FixtureRequest, tmp_path: Path, base_url: str) -> Any:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        inst = await pool.launch(kind=request.param, headed=False, url=base_url + "/closed")
    except Exception as exc:  # engine not installed on this host
        await pool.shutdown()
        pytest.skip(f"{request.param} unavailable: {exc}")
    try:
        yield pool.get(inst["instance_id"])
    finally:
        await pool.close(inst["instance_id"], force=True)
        await pool.shutdown()


async def _open(session: Any, url: str) -> None:
    await session.page.goto(url)
    # The badge is what makes the body scan walk its children (finding A);
    # without it the page would be read with a single innerText.
    assert await session.page.evaluate("() => !!document.getElementById('__octowright_badge__')")


async def test_a_display_contents_wrapper_draws_its_children(session: Any, base_url: str) -> None:
    await _open(session, base_url + "/contents")
    with pytest.raises(RuntimeError, match=r"\(script scan\)"):
        await session.expect_no_text("TOKEN-CONTENTS")
    await session.expect_no_text("TOKEN-CONTENTS-HIDDEN")


@pytest.mark.parametrize(
    ("token", "selector"),
    [
        ("TOKEN-MATCH-HIDDEN", ".t"),  # the match itself is display:none
        ("TOKEN-HIDDEN-HOST", "body"),  # a display:none host's open shadow root
        ("TOKEN-SHADOW-CSS", "body"),  # a shadow <style>'s source
        ("TOKEN-HIDDEN-FRAME", "body"),  # a display:none iframe's document
    ],
)
async def test_what_is_not_rendered_is_not_drawn(session: Any, base_url: str, token: str, selector: str) -> None:
    await _open(session, base_url + "/unrendered")
    result = await session.expect_no_text(token, selector=selector)
    assert result["matched"] >= 1


@pytest.mark.parametrize(
    "token",
    [
        "TOKEN-IMG-SRC",
        "TOKEN-DATA-ATTR",
        "TOKEN-STYLE-IMAGE",
        "TOKEN-PLACEHOLDER-UNDER-VALUE",
        "TOKEN-LINK-HREF",
        "TOKEN-TITLE",
        "5550137788",  # the digits of a drawn number, but not the text drawn
    ],
)
async def test_attribute_and_address_text_passes_on_every_engine(session: Any, base_url: str, token: str) -> None:
    await _open(session, base_url + "/attributes")
    result = await session.expect_no_text(token)
    assert result["snapshot"] == ("checked" if session.kind == "chromium" else "unsupported")


@pytest.mark.parametrize("token", ["TOKEN-ALT-BROKEN", "TOKEN-OPTION-A"])
async def test_drawn_attribute_text_fails_on_every_engine(session: Any, base_url: str, token: str) -> None:
    await _open(session, base_url + "/attributes")
    with pytest.raises(RuntimeError, match=r"\(script scan\)"):
        await session.expect_no_text(token)


async def test_a_closed_shadow_root_fails_only_on_chromium(session: Any, base_url: str) -> None:
    """Documented: script cannot reach a closed root; only Chromium's DOM snapshot can."""
    await _open(session, base_url + "/closed")
    if session.kind != "chromium":
        assert (await session.expect_no_text("TOKEN-CLOSED"))["snapshot"] == "unsupported"
        return
    with pytest.raises(RuntimeError, match=r"\(DOM snapshot\)"):
        await session.expect_no_text("TOKEN-CLOSED")


async def test_an_overlay_collision_does_not_skip_the_snapshot(session: Any, base_url: str) -> None:
    """E: the badge shows the forbidden word AND the page draws it in a closed root."""
    if session.kind != "chromium":
        pytest.skip("closed shadow roots are only reachable through Chromium's DOM snapshot")
    await _open(session, base_url + "/closed")
    badge = await session.page.evaluate("() => document.getElementById('__octowright_badge__').innerText")
    word = max(badge.split(), key=len)
    assert (await session.expect_no_text(word))["snapshot"] == "checked"  # the badge alone passes
    await session.page.evaluate(
        "(w) => document.body.appendChild(document.createElement('div'))"
        ".attachShadow({mode: 'closed'}).textContent = w",
        word,
    )
    with pytest.raises(RuntimeError, match=r"\(DOM snapshot\)"):
        await session.expect_no_text(word)


async def test_a_page_past_the_element_limit_is_refused(session: Any, base_url: str) -> None:
    await _open(session, base_url + "/huge")
    with pytest.raises(RuntimeError, match=str(ELEMENT_LIMIT)):
        await session.expect_no_text("TOKEN-NOT-THERE")
    result = await session.expect_no_text("TOKEN-NOT-THERE", selector="#small")
    assert result == {**result, "matched": 1, "truncated": False}


@pytest.mark.parametrize(
    ("path", "token", "drawn"),
    [
        ("/contents", "TOKEN-CONTENTS", True),
        ("/attributes", "TOKEN-ALT-BROKEN", True),
        ("/attributes", "TOKEN-IMG-SRC", False),
        ("/attributes", "5550137788", False),
        ("/unrendered", "TOKEN-HIDDEN-HOST", False),
        # Documented gap: the export has no CDP session, so no DOM snapshot.
        ("/closed", "TOKEN-CLOSED", False),
    ],
)
async def test_the_exported_cli_agrees_on_a_real_page(base_url: str, path: str, token: str, drawn: bool) -> None:
    """Same collector, same verdict, run by the generated script against real Chromium."""
    from octowright.artifacts.script_export import render_macro_cli

    actions = [{"action": "navigate", "url": base_url + path}, {"action": "expect_no_text", "text": token}]
    namespace: dict[str, Any] = {}
    exec(render_macro_cli(name="m", macro={"actions": actions}, include_evidence=False), namespace)
    try:
        run = namespace["run_m"]()
        if drawn:
            with pytest.raises(RuntimeError, match=r"forbidden text .*\(script scan\)"):
                await run
        else:
            assert (await run)["executed"] == 2
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip(f"chromium unavailable: {exc}")
        raise
