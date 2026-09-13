# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Redacted screenshots against a real page: every rendered spelling goes, the page comes back."""

from __future__ import annotations

import contextlib
import json
import urllib.parse
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from octowright.macros.safe_screenshot import redacted_screenshot

pytestmark = pytest.mark.live_browser

EMAIL = "c9-live-redaction-canary@example.test"

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


class _PageSession:
    def __init__(self, page: object) -> None:
        self.page = page

    @contextlib.asynccontextmanager
    async def operation(self, _name: str) -> AsyncIterator[None]:
        yield

    async def screenshot(self, path: Path) -> Path:
        await self.page.screenshot(path=str(path))  # type: ignore[attr-defined]
        return path


_CONTENT = f"""
<main>
  <p id="text">Signed in as {EMAIL}</p>
  <a id="link" href="https://app.test/profile?email={urllib.parse.quote(EMAIL, safe="")}">profile</a>
  <span id="attr" data-owner="{EMAIL}">owner</span>
  <input id="field" type="text">
  <script type="application/json" id="json">{json.dumps({"email": EMAIL})}</script>
  <div id="host"></div>
  <canvas id="drawing" width="10" height="10"></canvas>
</main>
"""

_STATE_JS = """() => ({
  html: document.documentElement.outerHTML,
  field: document.querySelector('#field').value,
  shadow: document.querySelector('#host').shadowRoot.innerHTML,
  canvasStyle: document.querySelector('#drawing').getAttribute('style'),
})"""


async def test_every_rendered_spelling_is_redacted_and_the_page_is_restored(tmp_path: Path) -> None:
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(_CONTENT)
                await page.evaluate(
                    """(email) => {
                      document.querySelector('#field').value = email;
                      const root = document.querySelector('#host').attachShadow({mode: 'open'});
                      root.innerHTML = `<b title="${email}">${email}</b>`;
                    }""",
                    EMAIL,
                )
                # Playwright's own screenshot hides the caret and leaves `style=""` on
                # inputs; take one plain screenshot first so the comparison below
                # measures only what the redaction changes.
                await page.screenshot(path=str(tmp_path / "warmup.png"))
                before = await page.evaluate(_STATE_JS)
                session = _PageSession(page)
                target = tmp_path / "shots" / "redacted.png"

                assert await redacted_screenshot(
                    session, {"action": "screenshot", "path": str(target)}, (EMAIL,), root=tmp_path
                ) == (1, 0)
                assert target.stat().st_size > 0
                assert await page.evaluate(_STATE_JS) == before

                # The in-page state is cleared, so a second screenshot on the same page works.
                second = tmp_path / "shots" / "again.png"
                assert await redacted_screenshot(
                    session, {"action": "screenshot", "path": str(second)}, (EMAIL,), root=tmp_path
                ) == (1, 0)
                assert await page.evaluate(_STATE_JS) == before
            finally:
                await browser.close()
    except Exception as exc:
        _skip_or_raise(exc)


async def test_the_redaction_removes_the_value_from_what_the_screenshot_sees(tmp_path: Path) -> None:
    from playwright.async_api import async_playwright

    from octowright.macros.privacy import sensitive_value_variants
    from octowright.macros.safe_screenshot import REDACT_RENDERED_JS, RESTORE_RENDERED_JS

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(_CONTENT)
                await page.evaluate("(email) => { document.querySelector('#field').value = email; }", EMAIL)
                await page.evaluate("() => { document.querySelector('#host').attachShadow({mode: 'open'}); }")
                remaining = await page.evaluate(REDACT_RENDERED_JS, list(sensitive_value_variants((EMAIL,))))
                try:
                    assert remaining == 0
                    during = await page.evaluate(_STATE_JS)
                    assert EMAIL not in during["html"]
                    assert urllib.parse.quote(EMAIL, safe="") not in during["html"]
                    assert during["field"] == "<redacted>"
                    assert "visibility: hidden" in (during["canvasStyle"] or "")
                finally:
                    await page.evaluate(RESTORE_RENDERED_JS)
            finally:
                await browser.close()
    except Exception as exc:
        _skip_or_raise(exc)
