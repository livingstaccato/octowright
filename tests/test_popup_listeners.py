# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import importlib
import os
import sys

import pytest


@pytest.fixture
def anyio_backend() -> str:
    # BrowserPool drives Playwright's asyncio API; the trio backend can crash
    # macOS WebKit before this test reaches the popup-listener path.
    return "asyncio"


# WebKit's popup behavior under virtualized CI runners differs from a real
# desktop session: programmatic popups (window.open from JS, no user gesture)
# get closed by the popup blocker before the first popup.evaluate() lands,
# producing a deterministic TargetClosedError. The product code (popup-
# listener registration in BrowserSession._register_popup) is correct;
# this is purely a CI-environment quirk. The test passes on a real
# developer machine, which is where it provides regression value.
@pytest.mark.skipif(
    os.environ.get("CI") == "true" and sys.platform == "darwin",
    reason="WebKit popup blocker on virtualized macOS CI runners closes "
    "programmatic about:blank popups; runs locally on real macOS desktops",
)
@pytest.mark.live_browser
@pytest.mark.anyio
async def test_popup_page_dialog_listener_fires(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "rec"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "prof"))
    from octowright import defaults as _defaults

    importlib.reload(_defaults)

    # Import after reload so RECORDINGS_DIR / PROFILES_DIR pick up the monkeypatched env.
    from octowright.browser_pool import BrowserPool

    pool = BrowserPool()
    r = await pool.launch(
        kind="webkit",
        url="about:blank",
        headed=False,
        label="pop",
        viewport_w=320,
        viewport_h=240,
        profile=None,
    )
    s = pool.get(r["instance_id"])
    await s.set_dialog_policy("accept")

    # Open a popup (without setting content yet — we want listeners wired first).
    await s.evaluate("window._p = window.open('about:blank', '_blank');")

    # Wait for popup to be tracked by the session (and listeners wired via _register_popup).
    for _ in range(40):
        if len(s.pages) > 1:
            break
        await asyncio.sleep(0.05)
    assert len(s.pages) == 2, "popup was not tracked"

    popup = s.pages[1]
    # Set HTML directly on the popup page *after* listeners are wired.
    await popup.evaluate(
        "document.getElementById('b') || ("
        "document.body.innerHTML = "
        "'<button id=b>go</button>',"
        "document.getElementById('b').onclick = "
        "() => { window.opener._ok = confirm('ok?'); }"
        ")"
    )

    # Click the button in the popup. Our dialog handler on the popup should accept
    # the confirm() — window._ok (in the opener) should become True.
    await popup.click("#b")
    # Wait for the dialog handler to accept + the onclick script to propagate
    # window._ok into the opener, instead of a fixed sleep.
    await s.page.wait_for_function("() => typeof window._ok !== 'undefined'", timeout=5000)

    result = await s.evaluate("window._ok")
    assert result is True, f"expected popup's confirm to be accepted; got {result!r}"

    await pool.close(r["instance_id"])
    await pool.shutdown()
