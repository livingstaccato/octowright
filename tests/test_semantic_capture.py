# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session.core import BrowserSession
from tests._aria_stubs import stub_credential_scan


@pytest.fixture
def mock_session(tmp_path: Path) -> BrowserSession:
    # Build a BrowserSession around a mock page
    # page.locator is SYNCHRONOUS in Playwright, so it must be a MagicMock.
    page = MagicMock()
    page.url = "https://octowright.com"

    recorder = MagicMock()

    session = BrowserSession(
        instance_id="test",
        kind="chromium",
        label="test-label",
        url="https://octowright.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "test.jsonl",
        recorder=recorder,
    )
    # Mock _target() to return self.page
    session._target = MagicMock(return_value=page)

    # Mock typical async methods that BrowserSession calls
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.type = AsyncMock()

    return session


@pytest.mark.asyncio
async def test_click_records_semantic_metadata(mock_session: BrowserSession):
    # Setup: Mock the aria_snapshot return value
    # page.locator(selector) returns a Locator object.
    locator_mock = MagicMock()
    locator_mock.aria_snapshot = AsyncMock(return_value='- button "Confirm Order"')
    stub_credential_scan(locator_mock)
    mock_session.page.locator.return_value = locator_mock

    # Action: Click the button
    await mock_session.click("#submit-btn")

    # Verification: Check recorder calls
    click_record = None
    for call in mock_session.recorder.record.call_args_list:
        args, kwargs = call
        if args[0] == "click":
            click_record = kwargs
            break

    assert click_record is not None
    assert click_record["selector"] == "#submit-btn"
    assert click_record["role"] == "button"
    assert click_record["role_name"] == "Confirm Order"


@pytest.mark.asyncio
async def test_fill_records_semantic_metadata(mock_session: BrowserSession):
    # Setup: Mock the aria_snapshot return value
    locator_mock = MagicMock()
    locator_mock.aria_snapshot = AsyncMock(return_value='- textbox "Email Address"')
    stub_credential_scan(locator_mock)
    mock_session.page.locator.return_value = locator_mock

    # Action: Fill the input
    await mock_session.fill("#email", "test@octowright.test")

    # Verification
    fill_record = None
    for call in mock_session.recorder.record.call_args_list:
        args, kwargs = call
        if args[0] == "fill":
            fill_record = kwargs
            break

    assert fill_record is not None
    assert fill_record["selector"] == "#email"
    assert fill_record["role"] == "textbox"
    assert fill_record["role_name"] == "Email Address"
