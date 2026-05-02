# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session.core import BrowserSession

# ---------------------------------------------------------------------------
# Fake Element — stub for Playwright ElementHandle.
# ---------------------------------------------------------------------------


class FakeElement:
    """Returned by wait_for_selector when an element should exist."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def inner_text(self) -> str:
        return self._text


@pytest.fixture
def mock_session(tmp_path: Path) -> BrowserSession:
    # Build a BrowserSession around an AsyncMock page
    page = AsyncMock()
    # default behavior for tests that don't override
    page.url = "https://example.com/path"

    session = BrowserSession(
        instance_id="test",
        kind="chromium",
        label="test-label",
        url="https://example.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "test.jsonl",
        recorder=MagicMock(),
    )
    return session


# ---------------------------------------------------------------------------
# expect_url tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_url_regex_match(mock_session: BrowserSession) -> None:
    mock_session.page.url = "https://example.com/dashboard"
    actual = await mock_session.expect_url(r"example\.com/dash", "regex")
    assert actual == "https://example.com/dashboard"


@pytest.mark.anyio
async def test_check_url_regex_mismatch(mock_session: BrowserSession) -> None:
    mock_session.page.url = "https://example.com/other"
    with pytest.raises(RuntimeError, match="regex"):
        await mock_session.expect_url(r"example\.com/dash", "regex")


@pytest.mark.anyio
async def test_check_url_equals_match(mock_session: BrowserSession) -> None:
    mock_session.page.url = "https://example.com/exact"
    actual = await mock_session.expect_url("https://example.com/exact", "equals")
    assert actual == "https://example.com/exact"


@pytest.mark.anyio
async def test_check_url_equals_mismatch(mock_session: BrowserSession) -> None:
    mock_session.page.url = "https://example.com/other"
    with pytest.raises(RuntimeError, match="equals"):
        await mock_session.expect_url("https://example.com/exact", "equals")


@pytest.mark.anyio
async def test_check_url_contains_match(mock_session: BrowserSession) -> None:
    mock_session.page.url = "https://example.com/path?q=1"
    actual = await mock_session.expect_url("/path", "contains")
    assert "/path" in actual


@pytest.mark.anyio
async def test_check_url_contains_mismatch(mock_session: BrowserSession) -> None:
    mock_session.page.url = "https://example.com/other"
    with pytest.raises(RuntimeError, match="contains"):
        await mock_session.expect_url("/dashboard", "contains")


@pytest.mark.anyio
async def test_check_url_bad_mode(mock_session: BrowserSession) -> None:
    with pytest.raises(ValueError, match="unknown mode"):
        await mock_session.expect_url("x", "bogus")


# ---------------------------------------------------------------------------
# expect_text tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_text_contains_pass(mock_session: BrowserSession) -> None:
    element = FakeElement("hello world")
    mock_session.page.wait_for_selector.return_value = element
    actual = await mock_session.expect_text("#x", "hello")
    assert "hello" in actual


@pytest.mark.anyio
async def test_check_text_contains_fail(mock_session: BrowserSession) -> None:
    element = FakeElement("goodbye")
    mock_session.page.wait_for_selector.return_value = element
    with pytest.raises(RuntimeError, match="#x"):
        await mock_session.expect_text("#x", "hello")


@pytest.mark.anyio
async def test_check_text_equals_pass(mock_session: BrowserSession) -> None:
    element = FakeElement("exact text")
    mock_session.page.wait_for_selector.return_value = element
    actual = await mock_session.expect_text("#x", "exact text", mode="equals")
    assert actual == "exact text"


@pytest.mark.anyio
async def test_check_text_equals_fail(mock_session: BrowserSession) -> None:
    element = FakeElement("other text")
    mock_session.page.wait_for_selector.return_value = element
    with pytest.raises(RuntimeError, match="equals"):
        await mock_session.expect_text("#x", "exact text", mode="equals")


@pytest.mark.anyio
async def test_check_text_regex_pass(mock_session: BrowserSession) -> None:
    element = FakeElement("Error code 42")
    mock_session.page.wait_for_selector.return_value = element
    actual = await mock_session.expect_text("#x", r"code \d+", mode="regex")
    assert "42" in actual


@pytest.mark.anyio
async def test_check_text_regex_fail(mock_session: BrowserSession) -> None:
    element = FakeElement("no digits here")
    mock_session.page.wait_for_selector.return_value = element
    with pytest.raises(RuntimeError, match="regex"):
        await mock_session.expect_text("#x", r"code \d+", mode="regex")


@pytest.mark.anyio
async def test_check_text_timeout_raises_runtime_error(mock_session: BrowserSession) -> None:
    mock_session.page.wait_for_selector.side_effect = Exception("Timeout 500ms exceeded")
    with pytest.raises(RuntimeError, match="never appeared"):
        await mock_session.expect_text("#missing", "hi", timeout_ms=500)


# ---------------------------------------------------------------------------
# expect_selector tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_selector_present_pass(mock_session: BrowserSession) -> None:
    element = FakeElement("content")
    mock_session.page.wait_for_selector.return_value = element
    # Should not raise
    await mock_session.expect_selector("#exists", present=True)


@pytest.mark.anyio
async def test_check_selector_present_fail(mock_session: BrowserSession) -> None:
    mock_session.page.wait_for_selector.side_effect = Exception("Timeout")
    with pytest.raises(RuntimeError, match="never appeared"):
        await mock_session.expect_selector("#missing", present=True, timeout_ms=100)


@pytest.mark.anyio
async def test_check_selector_absent_pass(mock_session: BrowserSession) -> None:
    # query_selector returns None → element absent → assertion passes
    mock_session.page.query_selector.return_value = None
    await mock_session.expect_selector("#gone", present=False)


@pytest.mark.anyio
async def test_check_selector_absent_fail(mock_session: BrowserSession) -> None:
    # query_selector returns a non-None object → element still there
    mock_session.page.query_selector.return_value = FakeElement("oops")
    with pytest.raises(RuntimeError, match="absent"):
        await mock_session.expect_selector("#still-here", present=False)


# ---------------------------------------------------------------------------
# expect_js tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_js_truthy_pass(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = True
    result = await mock_session.expect_js("1 === 1")
    assert result is True


@pytest.mark.anyio
async def test_check_js_truthy_fail(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = False
    with pytest.raises(RuntimeError, match="not truthy"):
        await mock_session.expect_js("false")


@pytest.mark.anyio
async def test_check_js_equals_pass(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = 42
    result = await mock_session.expect_js("40 + 2", equals=42)
    assert result == 42


@pytest.mark.anyio
async def test_check_js_equals_fail(mock_session: BrowserSession) -> None:
    mock_session.page.evaluate.return_value = 99
    with pytest.raises(RuntimeError, match="expected=42"):
        await mock_session.expect_js("something", equals=42)


@pytest.mark.anyio
async def test_check_js_falsy_none_equals_raises(mock_session: BrowserSession) -> None:
    """evaluate returns 0 (falsy, no equals) → RuntimeError."""
    mock_session.page.evaluate.return_value = 0
    with pytest.raises(RuntimeError, match="not truthy"):
        await mock_session.expect_js("0")
