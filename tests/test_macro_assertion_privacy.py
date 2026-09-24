# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a macro failure hands back, and how a recorded expect_no_text saves.

- ``failed_requests`` in every failure payload keeps origin and path but drops
  userinfo, query and fragment: an app-generated token in a query string is not
  a macro argument, so the argument scrubber never knew to remove it. This was
  a payload-wide leak that predated the new assertions; ``expect_network_clean``
  made it visible by promising counts only.
- ``page_errors`` in the payload names the uncaught exceptions that an
  ``N page error(s)`` failure counted -- they are not console messages, so the
  console tail never showed them.
- ``expect_no_text`` records its own marker, not the password-field one, so a
  recording containing it saves and a login's password fill still binds.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.defaults import REDACTED_ASSERTION_TEXT, REDACTED_INPUT_PLACEHOLDER
from octowright.macros import execution
from octowright.macros.execution import _failed_requests_tail
from octowright.macros.lint import lint_macro
from octowright.session.core import BrowserSession

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


@pytest.fixture
def session(tmp_path: Path) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://octowright.com/"
    page.content = AsyncMock(return_value="<html></html>")
    return BrowserSession(
        instance_id="test",
        kind="chromium",
        label="t",
        url="https://octowright.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )


def _rows(*urls: str) -> MagicMock:
    session = MagicMock()
    rows = [{"url": url, "status": None, "failure": "net::ERR_FAILED"} for url in urls]
    session.get_network_requests = MagicMock(return_value={"requests": rows})
    return session


# --- E: request URLs in the failure payload --------------------------------------------


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("https://user:pw@api.test/v1/orders?token=abc#frag", "https://api.test/v1/orders"),
        ("https://api.test:8443/x?session=s3cr3t", "https://api.test:8443/x"),
        ("/relative/path?sig=deadbeef", "/relative/path"),
        ("https://api.test/plain", "https://api.test/plain"),
    ],
)
def test_failed_request_urls_lose_userinfo_query_and_fragment(url: str, kept: str) -> None:
    assert [row["url"] for row in _failed_requests_tail(_rows(url))] == [kept]


def test_an_unparsable_url_is_dropped_to_nothing_sensitive() -> None:
    [row] = _failed_requests_tail(_rows("http://[::1/bad?token=abc"))
    assert "token" not in row["url"]


def test_the_session_deque_itself_is_not_rewritten() -> None:
    session = _rows("https://api.test/x?token=abc")
    _failed_requests_tail(session)
    assert session.get_network_requests.return_value["requests"][0]["url"].endswith("token=abc")


def _load(monkeypatch: pytest.MonkeyPatch, macros: dict[str, list[dict[str, Any]]]) -> None:
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})


@pytest.mark.anyio
async def test_a_network_clean_failure_payload_carries_no_query_token(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = MagicMock(url="https://api.test/x?token=abc", method="GET", resource_type="fetch")
    request.failure = "net::ERR_CONNECTION_REFUSED"
    request.headers = {}
    _load(monkeypatch, {"m": [{"action": "evaluate", "expression": "1"}, {"action": "expect_network_clean"}]})

    def evaluate(expression: Any, *_a: Any, **_k: Any) -> None:
        if expression == "1":
            session._handle_request_failed(request)

    session.page.evaluate = AsyncMock(side_effect=evaluate)
    with pytest.raises(RuntimeError) as excinfo:
        await execution.run_macro(session, "m")
    payload = excinfo.value.args[0]
    assert "token" not in json.dumps(payload, default=str)
    assert payload["failed_requests"][0]["url"] == "https://api.test/x"


# --- L: page errors named in the failure payload ------------------------------------------


@pytest.mark.anyio
async def test_the_failure_payload_names_the_page_errors_it_counted(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _load(monkeypatch, {"m": [{"action": "evaluate", "expression": "1"}, {"action": "expect_network_clean"}]})

    def evaluate(expression: Any, *_a: Any, **_k: Any) -> None:
        # Only the macro's own step; the status pill evaluates on the page too.
        if expression == "1":
            session._handle_page_error(Exception("TypeError: x is undefined"))

    session.page.evaluate = AsyncMock(side_effect=evaluate)
    with pytest.raises(RuntimeError) as excinfo:
        await execution.run_macro(session, "m")
    assert excinfo.value.args[0]["page_errors"] == [{"message": "TypeError: x is undefined"}]


@pytest.mark.anyio
async def test_page_errors_in_the_payload_are_bounded_and_scrubbed(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(30):
        session._handle_page_error(Exception(f"e{i}"))
    session._handle_page_error(Exception(f"leaked {SECRET}"))
    _load(monkeypatch, {"m": [{"action": "expect_js", "expression": "false", "password": "{{password}}"}]})
    session.page.evaluate = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError) as excinfo:
        await execution.run_macro(session, "m", {"password": SECRET})
    errors = excinfo.value.args[0]["page_errors"]
    assert len(errors) == execution.MACRO_FAILURE_PAGE_ERROR_TAIL
    assert SECRET not in json.dumps(errors)


# --- B: expect_no_text's recording marker ----------------------------------------------


def test_the_assertion_marker_is_not_the_password_field_marker() -> None:
    assert REDACTED_ASSERTION_TEXT != REDACTED_INPUT_PLACEHOLDER


@pytest.mark.anyio
async def test_expect_no_text_records_its_own_marker(session: BrowserSession) -> None:
    session.page.evaluate = AsyncMock(return_value={"pieces": ["nothing secret"], "overlay": "", "matched": 1})
    session._snapshot_leaks = AsyncMock(return_value=[])  # type: ignore[method-assign]
    await session.expect_no_text(SECRET)
    kwargs = session.recorder.record.call_args.kwargs
    assert kwargs["text"] == REDACTED_ASSERTION_TEXT


@pytest.mark.anyio
async def test_replaying_the_marker_itself_is_refused(session: BrowserSession) -> None:
    """Checking that the literal marker is absent would always pass and prove nothing."""
    with pytest.raises(ValueError, match="redacted"):
        await session.expect_no_text(REDACTED_ASSERTION_TEXT)


def test_lint_flags_an_unbound_assertion_marker() -> None:
    issues = lint_macro({"name": "m", "actions": [{"action": "expect_no_text", "text": REDACTED_ASSERTION_TEXT}]})
    assert any(i.severity == "error" and "redacted" in i.message for i in issues)


def _storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros.storage as storage

    importlib.reload(storage)
    return storage


def _recording(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "recording.jsonl"
    path.write_text("\n".join(json.dumps({"ts": "2026-09-24T10:00:00Z", **r}) for r in rows), encoding="utf-8")
    return path


def _saved_actions(saved: Path) -> list[dict[str, Any]]:
    return json.loads(saved.read_text(encoding="utf-8"))["actions"]


def test_a_recording_with_the_assertion_saves_without_a_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = _storage(monkeypatch, tmp_path)
    rows = [
        {"action": "navigate", "url": "https://x.test/"},
        {"action": "expect_no_text", "selector": "body", "text": REDACTED_ASSERTION_TEXT},
    ]
    saved = storage.save_macro(recording_path=_recording(tmp_path, rows), name="m")
    assert _saved_actions(saved)[1]["text"] == REDACTED_ASSERTION_TEXT


def test_a_login_recording_binds_both_the_fill_and_the_assertion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ordinary case: fill the password, then check it is not rendered."""
    storage = _storage(monkeypatch, tmp_path)
    rows = [
        {"action": "fill", "selector": "#pw", "value": REDACTED_INPUT_PLACEHOLDER},
        {"action": "expect_no_text", "selector": "body", "text": REDACTED_ASSERTION_TEXT},
    ]
    saved = storage.save_macro(recording_path=_recording(tmp_path, rows), name="m", parameters={"password": SECRET})
    actions = _saved_actions(saved)
    assert actions[0]["value"] == "{{password}}"
    assert actions[1]["text"] == "{{password}}"


def test_two_credential_parameters_leave_the_assertion_for_the_author(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = _storage(monkeypatch, tmp_path)
    rows = [{"action": "expect_no_text", "selector": "body", "text": REDACTED_ASSERTION_TEXT}]
    saved = storage.save_macro(
        recording_path=_recording(tmp_path, rows), name="m", parameters={"password": SECRET, "api_token": "t0k3n-xyz"}
    )
    assert _saved_actions(saved)[0]["text"] == REDACTED_ASSERTION_TEXT
