# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""End-to-end proof that a filled password never reaches an aria snapshot.

The unit tests cover the scrubber in isolation; these drive a real Chromium
so the assertion still holds if Playwright changes how it renders a
credential control into the accessibility tree.
"""

from __future__ import annotations

import contextlib

import pytest

from octowright.defaults import REDACTED_INPUT_PLACEHOLDER
from octowright.session import aria_redaction as ar


class _Session:
    """Minimal session: the scrubber only needs a re-entrant lease."""

    @contextlib.asynccontextmanager
    async def operation(self, _name: str):
        yield


SESSION = _Session()

pytestmark = pytest.mark.live_browser

PASSWORD = "hunter2-SUPERSECRET"  # pragma: allowlist secret (synthetic fixture)
OTP = "otp-9876-SECRET"
USERNAME = "tanuki-tim"

FORM = """<html><body><form>
  <input id="u" type="text">
  <input id="p" type="password">
  <input id="c" type="text" autocomplete="current-password">
  <div id="d" contenteditable autocomplete="new-password"></div>
</form></body></html>"""


@pytest.fixture
async def filled_page():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(FORM)
        await page.fill("#u", USERNAME)
        await page.fill("#p", PASSWORD)
        await page.fill("#c", OTP)
        yield page
        await browser.close()


async def test_raw_playwright_snapshot_leaks(filled_page) -> None:
    """Characterization: this is the bug the scrubber exists to fix.

    If Playwright ever stops putting the value in the tree this test fails,
    which is the signal to re-check whether the scrubber is still needed.
    """
    raw = await filled_page.locator("html").aria_snapshot()
    assert PASSWORD in raw
    assert OTP in raw


async def test_scrubbed_snapshot_hides_credentials(monkeypatch, filled_page) -> None:
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "passwords")
    aria = await ar.aria_snapshot(SESSION, filled_page.locator("html"))
    assert PASSWORD not in aria
    assert OTP not in aria
    assert REDACTED_INPUT_PLACEHOLDER in aria
    # The non-credential field is still legible -- the tree stays useful.
    assert USERNAME in aria


async def test_all_mode_also_hides_the_username(monkeypatch, filled_page) -> None:
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "all")
    aria = await ar.aria_snapshot(SESSION, filled_page.locator("html"))
    assert USERNAME not in aria
    assert PASSWORD not in aria


async def test_off_mode_is_an_explicit_opt_out(monkeypatch, filled_page) -> None:
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "off")
    aria = await ar.aria_snapshot(SESSION, filled_page.locator("html"))
    assert PASSWORD in aria


async def test_single_element_snapshot_is_scrubbed(monkeypatch, filled_page) -> None:
    """The click-metadata path snapshots one element, not the document root."""
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "passwords")
    aria = await ar.aria_snapshot(SESSION, filled_page.locator("#p"))
    assert PASSWORD not in aria
    assert REDACTED_INPUT_PLACEHOLDER in aria
