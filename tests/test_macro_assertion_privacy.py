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
from octowright.macros import execution, failure_context
from octowright.macros.failure_context import failed_requests_tail as _failed_requests_tail
from octowright.macros.lint import lint_macro
from octowright.session.core import BrowserSession

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


@pytest.fixture
def session(tmp_path: Path) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://octowright.com/"
    page.content = AsyncMock(return_value="<html></html>")
    page.frames = [page]  # a real page lists its main frame; the fake is its own
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


@pytest.mark.parametrize("url", ["http://[::1/bad?token=abc", "https://user:pw@:99999/x", "https://s3cr3t@/x"])
def test_a_url_with_no_usable_host_is_reported_as_invalid(url: str) -> None:
    """A netloc with no host or a bad port is not guessed at; the macro digest reports it the same way."""
    [row] = _failed_requests_tail(_rows(url))
    assert row["url"] == "(invalid-url)"


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
    # Page code in a run carrying a credential is refused by the sink guard;
    # this test is about the failure payload's scrubbing, which holds either way.
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", "allow")
    _load(monkeypatch, {"m": [{"action": "expect_js", "expression": "false", "password": "{{password}}"}]})
    session.page.evaluate = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError) as excinfo:
        await execution.run_macro(session, "m", {"password": SECRET})
    errors = excinfo.value.args[0]["page_errors"]
    assert len(errors) == failure_context.MACRO_FAILURE_PAGE_ERROR_TAIL
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
        _assertion_row(SECRET),
    ]
    saved = storage.save_macro(recording_path=_recording(tmp_path, rows), name="m", parameters={"password": SECRET})
    actions = _saved_actions(saved)
    assert actions[0]["value"] == "{{password}}"
    assert actions[1]["text"] == "{{password}}"
    assert "text_digest" not in actions[1]


def test_a_saved_assertion_keeps_its_inputs_and_drops_what_it_observed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from octowright.drawn_text import NO_TEXT_OBSERVATION_KEYS

    storage = _storage(monkeypatch, tmp_path)
    row = {**_assertion_row(SECRET), "element_limit": 50, "matched": 1, "frames_scanned": 1}
    row |= {"frames_skipped": 0, "truncated": False, "snapshot": "checked"}
    saved = storage.save_macro(recording_path=_recording(tmp_path, [row]), name="m", parameters={"password": SECRET})
    action = {key: value for key, value in _saved_actions(saved)[0].items() if key != "ts"}
    assert action == {"action": "expect_no_text", "selector": "body", "text": "{{password}}", "element_limit": 50}
    assert not set(action) & set(NO_TEXT_OBSERVATION_KEYS)


def _assertion_row(text: str | None) -> dict[str, Any]:
    from octowright.macros.privacy import assertion_text_digest

    row: dict[str, Any] = {"action": "expect_no_text", "selector": "body", "text": REDACTED_ASSERTION_TEXT}
    if text is not None:
        row["text_digest"] = assertion_text_digest(text)
    return row


@pytest.mark.anyio
async def test_expect_no_text_records_a_keyed_digest_not_the_text(session: BrowserSession) -> None:
    from octowright.macros.privacy import assertion_text_digest

    session.page.evaluate = AsyncMock(return_value={"pieces": ["nothing secret"], "overlay": "", "matched": 1})
    session._snapshot_leaks = AsyncMock(return_value=[])  # type: ignore[method-assign]
    await session.expect_no_text(SECRET)
    kwargs = session.recorder.record.call_args.kwargs
    assert kwargs["text_digest"] == assertion_text_digest(SECRET)
    assert SECRET not in json.dumps(kwargs)
    # Compared as the check compares: case and invisible characters do not matter.
    assert assertion_text_digest(SECRET.upper() + "\u200b") == kwargs["text_digest"]


def test_a_traceback_check_is_not_turned_into_a_password_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only the marker whose digest matches the password binds to it."""
    storage = _storage(monkeypatch, tmp_path)
    rows = [
        _assertion_row("Traceback"),
        {"action": "fill", "selector": "#pw", "value": REDACTED_INPUT_PLACEHOLDER},
        _assertion_row(SECRET),
    ]
    saved = storage.save_macro(recording_path=_recording(tmp_path, rows), name="m", parameters={"password": SECRET})
    actions = _saved_actions(saved)
    assert actions[0]["text"] == REDACTED_ASSERTION_TEXT
    assert actions[2]["text"] == "{{password}}"
    assert all("text_digest" not in action for action in actions)
    assert any(i.severity == "error" and "redacted" in i.message for i in lint_macro({"name": "m", "actions": actions}))


@pytest.mark.parametrize("digest_text", [None, "something else"], ids=["absent", "mismatched"])
def test_an_unmatched_digest_leaves_the_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, digest_text: str | None
) -> None:
    """Absent is also what a recording from before a daemon restart looks like to the new key."""
    storage = _storage(monkeypatch, tmp_path)
    saved = storage.save_macro(
        recording_path=_recording(tmp_path, [_assertion_row(digest_text)]), name="m", parameters={"password": SECRET}
    )
    assert _saved_actions(saved)[0]["text"] == REDACTED_ASSERTION_TEXT


def test_a_recording_from_a_previous_daemon_does_not_bind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from octowright.macros import privacy

    row = _assertion_row(SECRET)
    monkeypatch.setattr(privacy, "_ASSERTION_DIGEST_KEY", b"a-new-process-key")
    storage = _storage(monkeypatch, tmp_path)
    saved = storage.save_macro(recording_path=_recording(tmp_path, [row]), name="m", parameters={"password": SECRET})
    assert _saved_actions(saved)[0]["text"] == REDACTED_ASSERTION_TEXT


def test_with_two_credential_parameters_the_assertion_binds_to_the_matching_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = _storage(monkeypatch, tmp_path)
    saved = storage.save_macro(
        recording_path=_recording(tmp_path, [_assertion_row("t0k3n-xyz")]),
        name="m",
        parameters={"password": SECRET, "api_token": "t0k3n-xyz"},  # pragma: allowlist secret
    )
    assert _saved_actions(saved)[0]["text"] == "{{api_token}}"


def test_a_non_credential_parameter_binds_when_its_value_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = _storage(monkeypatch, tmp_path)
    saved = storage.save_macro(
        recording_path=_recording(tmp_path, [_assertion_row("123-45-6789")]),
        name="m",
        parameters={"forbidden": "123-45-6789"},
    )
    assert _saved_actions(saved)[0]["text"] == "{{forbidden}}"


# --- failure-context producers log what they swallow -------------------------------------


@pytest.mark.parametrize("producer", ["page_errors_tail", "failed_requests_tail"])
def test_a_failing_failure_context_producer_is_logged_not_silent(
    monkeypatch: pytest.MonkeyPatch, producer: str
) -> None:
    from types import SimpleNamespace

    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(failure_context, "log", SimpleNamespace(debug=lambda event, **kw: events.append((event, kw))))

    class _Broken:
        @property
        def page_errors(self) -> list[Any]:
            raise RuntimeError(f"cannot read {SECRET}")

        def get_network_requests(self, **_kw: Any) -> Any:
            raise RuntimeError(f"cannot read {SECRET}")

    assert getattr(failure_context, producer)(_Broken()) == []  # type: ignore[arg-type]
    assert len(events) == 1 and events[0][1] == {"error_type": "RuntimeError"}
