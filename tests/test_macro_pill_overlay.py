# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The macro status pill must not collide with the page's own text locators.

During replay the pill echoes the action it is about to run into a visible
label (e.g. ``order_brightmart | click_by text=Place order``). When that label
lives in the light DOM, a macro step that resolves ``get_by_text("Place
order")`` matches BOTH the real button and the pill's label — a Playwright
strict-mode violation, so the macro's own instrumentation breaks the macro.

The pill renders inside a *closed* shadow root, which Playwright's text/role/css
locators cannot pierce, so the only ``Place order`` match is the real target.
"""

from __future__ import annotations

import json

import pytest

from octowright.browser_pool.visuals import _macro_status_script

pytestmark = pytest.mark.live_browser

# Substrings that mean "no real browser engine here" — skip rather than fail so
# the suite stays green in environments without Playwright engines installed.
_NO_ENGINE = (
    "executable doesn't exist",
    "browser has been closed",
    "target page, context or browser has been closed",
    "missing x server",
    "no protocol specified",
    "playwright install",
)


def _skip_or_raise(exc: Exception) -> None:
    if any(snippet in str(exc).lower() for snippet in _NO_ENGINE):
        pytest.skip(f"live browser engine unavailable: {exc}")
    raise exc


def _pill_script() -> str:
    """The production pill asset with its identity placeholders filled in."""
    return _macro_status_script().replace("__ID_TAG__", json.dumps("🐢")).replace("__ID_COLOR__", json.dumps("#334455"))


async def test_pill_label_does_not_collide_with_text_locator() -> None:
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('<button id="real">Place order</button>')

                # Define window.__octowright_macro_status (the IIFE asset) and push
                # a status whose text echoes the action that collided in the field.
                await page.evaluate(_pill_script())
                await page.evaluate(
                    "window.__octowright_macro_status("
                    "{start: true, text: 'order_brightmart | click_by text=Place order'})"
                )

                # The pill host renders (so we are not 'fixing' it by hiding it)...
                assert await page.locator("#__octowright_macro_status__").count() == 1
                # ...but its echoed label must be invisible to page locators, so the
                # only "Place order" match is the real button.
                assert await page.get_by_text("Place order").count() == 1
            finally:
                await browser.close()
    except Exception as exc:  # pragma: no cover - environment-dependent skip
        _skip_or_raise(exc)


async def test_pill_still_works_inside_closed_shadow() -> None:
    """Moving the contents into a closed shadow root must not break the pill."""
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("<main>page body</main>")
                await page.evaluate(_pill_script())
                await page.evaluate(
                    "window.__octowright_macro_status({start: true, text: 'flow | click_by text=Submit'})"
                )

                host = page.locator("#__octowright_macro_status__")
                # show() completed (it sets the label inside the shadow first): the
                # pill is visible, proving the shadow-scoped query did not throw.
                assert await host.evaluate("el => el.style.opacity") == "0.7"
                # The shadow root is genuinely CLOSED — not exposed to the page.
                assert await host.evaluate("el => el.shadowRoot") is None
                # The click-to-open history modal still works and carries the text.
                await host.dispatch_event("click")
                modal = page.locator("#__octowright_macro_modal__")
                await modal.wait_for(state="attached", timeout=2000)
                assert "click_by text=Submit" in await modal.inner_text()
            finally:
                await browser.close()
    except Exception as exc:  # pragma: no cover - environment-dependent skip
        _skip_or_raise(exc)
