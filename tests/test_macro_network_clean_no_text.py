# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``expect_network_clean`` and ``expect_no_text``: two negative assertions.

Both exist so a journey check that today lives outside octowright can be a
macro. Each one's failure message is part of the contract: network-clean
reports counts only (a failed URL can carry a token in its query string), and
no-text never repeats the text it was asked about (it is usually a secret).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool.listeners import _wire_listeners
from octowright.defaults import REDACTED_ASSERTION_TEXT
from octowright.macros.lint import lint_macro
from octowright.macros.runtime import _ACTION_MAP
from octowright.macros.substitution import substitute
from octowright.session.core import BrowserSession

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


@pytest.fixture
def session(tmp_path: Path) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://octowright.com/"
    page.on = MagicMock()  # Page.on is synchronous; an AsyncMock one leaks unawaited coroutines
    page.frames = [page]  # a real page lists its main frame; the fake is its own
    return BrowserSession(
        instance_id="test",
        kind="chromium",
        label="test-label",
        url="https://octowright.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "test.jsonl",
        recorder=MagicMock(),
    )


def _fail(session: BrowserSession, failure: str, url: str = "https://api.test/x?token=abc") -> None:
    request = MagicMock(url=url, method="GET", resource_type="fetch", failure=failure)
    request.all_headers = MagicMock(return_value={})
    request.headers = {}
    session._handle_request_failed(request)


# ---------------------------------------------------------------------------
# expect_network_clean
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_network_clean_passes_on_a_quiet_page(session: BrowserSession) -> None:
    result = await session.expect_network_clean()
    assert result == {"failed_requests": 0, "page_errors": 0, "in_flight": 0}


@pytest.mark.anyio
async def test_a_failed_request_fails_with_counts_only(session: BrowserSession) -> None:
    _fail(session, "net::ERR_CONNECTION_REFUSED")
    with pytest.raises(RuntimeError) as excinfo:
        await session.expect_network_clean()
    message = str(excinfo.value)
    assert "1 failed request" in message
    # Counts only: a failed URL can carry a credential in its query string.
    assert "api.test" not in message and "token" not in message


@pytest.mark.anyio
@pytest.mark.parametrize("aborted", ["net::ERR_ABORTED", "NS_BINDING_ABORTED", "Load request cancelled"])
async def test_an_aborted_request_is_not_a_failure(session: BrowserSession, aborted: str) -> None:
    """Navigating away cancels in-flight requests; that is not the page failing."""
    _fail(session, aborted)
    assert await session.expect_network_clean() == {"failed_requests": 0, "page_errors": 0, "in_flight": 0}


@pytest.mark.anyio
async def test_a_page_error_fails_without_its_message(session: BrowserSession) -> None:
    session._handle_page_error(Exception(f"TypeError: cannot read {SECRET}"))
    with pytest.raises(RuntimeError) as excinfo:
        await session.expect_network_clean()
    assert "1 page error" in str(excinfo.value)
    assert SECRET not in str(excinfo.value)


@pytest.mark.anyio
async def test_only_failures_after_the_mark_count(session: BrowserSession) -> None:
    """A macro is judged on what happened while it ran, not on the session's past."""
    _fail(session, "net::ERR_CONNECTION_REFUSED")
    session._handle_page_error(Exception("boom"))
    session.mark_network_clean_window()
    assert await session.expect_network_clean() == {"failed_requests": 0, "page_errors": 0, "in_flight": 0}
    _fail(session, "net::ERR_NAME_NOT_RESOLVED")
    with pytest.raises(RuntimeError, match="1 failed request"):
        await session.expect_network_clean()


@pytest.mark.anyio
async def test_http_error_status_is_not_a_request_failure(session: BrowserSession) -> None:
    """A 404/500 got an answer; Playwright's requestfailed is transport failure only."""
    response = MagicMock(status=500, status_text="Server Error")
    response.request = MagicMock(url="https://x.test/", method="GET", resource_type="fetch")
    response.request.all_headers = MagicMock(return_value={})
    response.request.headers = {}
    session._handle_response(response)
    assert (await session.expect_network_clean())["failed_requests"] == 0


@pytest.mark.anyio
async def test_network_clean_is_recorded(session: BrowserSession) -> None:
    await session.expect_network_clean()
    session.recorder.record.assert_called_with("expect_network_clean")


def test_page_errors_are_bounded(session: BrowserSession) -> None:
    for i in range(session.page_errors.maxlen + 5):  # type: ignore[operator]
        session._handle_page_error(Exception(f"e{i}"))
    assert len(session.page_errors) == session.page_errors.maxlen
    assert session.page_error_count == session.page_errors.maxlen + 5  # type: ignore[operator]


def test_pageerror_is_wired_on_every_page(session: BrowserSession) -> None:
    page = MagicMock()
    _wire_listeners(session, page)
    events = [call.args[0] for call in page.on.call_args_list]
    assert "pageerror" in events


# ---------------------------------------------------------------------------
# expect_no_text
# ---------------------------------------------------------------------------


def _drawn(session: BrowserSession, *pieces: str) -> AsyncMock:
    """Have the page's rendered-text scan return *pieces*; stub Chromium's snapshot."""
    scan = AsyncMock(return_value={"pieces": list(pieces), "overlay": "", "matched": 1})
    session.page.evaluate = scan
    session._snapshot_leaks = AsyncMock(return_value=False)  # type: ignore[method-assign]
    return scan


@pytest.mark.anyio
async def test_no_text_passes_when_absent(session: BrowserSession) -> None:
    scan = _drawn(session, "Welcome back")
    await session.expect_no_text(SECRET)
    assert scan.call_args.args[1]["selector"] == "body"


@pytest.mark.anyio
async def test_no_text_fails_without_repeating_the_text(session: BrowserSession) -> None:
    _drawn(session, f"Your password is {SECRET}")
    with pytest.raises(RuntimeError) as excinfo:
        await session.expect_no_text(SECRET)
    assert SECRET not in str(excinfo.value)
    assert "body" in str(excinfo.value)


@pytest.mark.anyio
async def test_no_text_ignores_case_and_invisible_characters(session: BrowserSession) -> None:
    """The screenshot scanner's comparison: a security check errs toward failing."""
    _drawn(session, SECRET.upper().replace("-", "-\u200b"))
    with pytest.raises(RuntimeError, match="forbidden text"):
        await session.expect_no_text(SECRET)


@pytest.mark.anyio
async def test_no_text_scopes_to_a_selector(session: BrowserSession) -> None:
    scan = _drawn(session)
    await session.expect_no_text(SECRET, selector="#profile")
    assert scan.call_args.args[1]["selector"] == "#profile"
    session._snapshot_leaks.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_the_snapshot_can_fail_what_script_cannot_see(session: BrowserSession) -> None:
    _drawn(session)
    session._snapshot_leaks = AsyncMock(return_value=True)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match=r"\(DOM snapshot\)"):
        await session.expect_no_text(SECRET)


@pytest.mark.anyio
async def test_no_text_rejects_empty_text(session: BrowserSession) -> None:
    """An empty string is in every page, so it would always fail -- say why instead."""
    with pytest.raises(ValueError, match="empty"):
        await session.expect_no_text("")


@pytest.mark.anyio
async def test_no_text_records_a_marker_not_the_text(session: BrowserSession) -> None:
    _drawn(session)
    await session.expect_no_text(SECRET, selector="#p")
    from octowright.macros.privacy import assertion_text_digest

    kwargs = session.recorder.record.call_args.kwargs
    assert session.recorder.record.call_args.args == ("expect_no_text",)
    assert (kwargs["selector"], kwargs["text"]) == ("#p", REDACTED_ASSERTION_TEXT)
    assert kwargs["text_digest"] == assertion_text_digest(SECRET)


# ---------------------------------------------------------------------------
# macro surface
# ---------------------------------------------------------------------------


def test_both_actions_replay() -> None:
    assert _ACTION_MAP["expect_network_clean"] == "expect_network_clean"
    assert _ACTION_MAP["expect_no_text"] == "expect_no_text"


def _errors(actions: list[dict[str, object]]) -> list[str]:
    return [i.message for i in lint_macro({"name": "m", "actions": actions}) if i.severity == "error"]


def test_lint_accepts_both_actions() -> None:
    assert _errors([{"action": "expect_network_clean"}, {"action": "expect_no_text", "text": "{{password}}"}]) == []


def test_lint_requires_text_on_no_text() -> None:
    assert any("text" in message for message in _errors([{"action": "expect_no_text"}]))


def test_a_credential_may_be_substituted_into_no_text() -> None:
    """Not a navigation or code sink, so the credential guard must let it through."""
    actions = substitute([{"action": "expect_no_text", "text": "{{password}}"}], {"password": SECRET})
    assert actions == [{"action": "expect_no_text", "text": SECRET}]


# ---------------------------------------------------------------------------
# expect_network_clean(http_errors=True)
# ---------------------------------------------------------------------------


def _respond(session: BrowserSession, status: int, resource_type: str = "fetch") -> None:
    response = MagicMock(status=status, status_text="x")
    response.request = MagicMock(url="https://x.test/api?token=abc", method="GET", resource_type=resource_type)
    response.request.headers = {}
    session._handle_response(response)


@pytest.mark.anyio
async def test_http_errors_are_off_by_default(session: BrowserSession) -> None:
    _respond(session, 500)
    assert await session.expect_network_clean() == {"failed_requests": 0, "page_errors": 0, "in_flight": 0}


@pytest.mark.anyio
@pytest.mark.parametrize("resource_type", ["document", "fetch", "xhr"])
@pytest.mark.parametrize("status", [404, 500])
async def test_http_errors_opt_in_counts_api_and_page_loads(
    session: BrowserSession, resource_type: str, status: int
) -> None:
    _respond(session, status, resource_type)
    with pytest.raises(RuntimeError) as excinfo:
        await session.expect_network_clean(http_errors=True)
    assert "1 HTTP error(s)" in str(excinfo.value)
    assert "x.test" not in str(excinfo.value) and "token" not in str(excinfo.value)


@pytest.mark.anyio
@pytest.mark.parametrize("resource_type", ["image", "font", "stylesheet", "media", "other"])
async def test_http_errors_ignore_cosmetic_resources(session: BrowserSession, resource_type: str) -> None:
    """A missing favicon or font is not the journey failing."""
    _respond(session, 404, resource_type)
    result = await session.expect_network_clean(http_errors=True)
    assert result == {"failed_requests": 0, "page_errors": 0, "http_errors": 0, "in_flight": 0}


@pytest.mark.anyio
async def test_http_errors_ignore_success_and_redirects(session: BrowserSession) -> None:
    for status in (200, 204, 301, 304):
        _respond(session, status)
    assert (await session.expect_network_clean(http_errors=True))["http_errors"] == 0


@pytest.mark.anyio
async def test_http_errors_respect_the_run_window(session: BrowserSession) -> None:
    _respond(session, 500)
    session.mark_network_clean_window()
    assert (await session.expect_network_clean(http_errors=True))["http_errors"] == 0


@pytest.mark.anyio
async def test_http_errors_option_is_recorded(session: BrowserSession) -> None:
    await session.expect_network_clean(http_errors=True)
    session.recorder.record.assert_called_with("expect_network_clean", http_errors=True)


def test_lint_accepts_the_http_errors_option() -> None:
    assert _errors([{"action": "expect_network_clean", "http_errors": True}]) == []
