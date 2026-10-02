# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A refused classified screenshot says why, and never what.

Field report: a sequence signed in with ``username`` as a credential argument,
then a ``screenshot`` macro failed with only "macro bmf-screenshot failed at
step 0 (screenshot)" -- the app's header rendered the username, the rendered
surface scan found it, and nothing told the operator so. The refusal now names
the kind of surface, the classified tier and the argument, in the step's error,
in a ``screenshot_refused`` payload field, and in one warning log event; the
value, the page text around it, and anything built from it appear in none.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import runner
from octowright.macros import execution, safe_screenshot, screenshot_refusal, sequence_steps
from octowright.macros.privacy import BLIND_SCRUB_POLICY_ENV, SESSION_PRIVACY_LEDGER_ATTR
from tests.test_macro_redacted_screenshot import FakePage, _session

USERNAME = "admin"  # the BMF lab's username: short, ordinary, rendered in the header
EMAIL = "b7-refusal-canary@example.test"
AROUND = "Signed in as"
POLICY_ENV = "OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS"


def _rendering(text: str) -> dict[str, Any]:
    return {"strings": [text], "documents": [{"nodes": {}, "layout": {"text": [0]}}]}


@pytest.fixture(autouse=True)
def _recordings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from octowright import defaults

    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path)
    monkeypatch.delenv(POLICY_ENV, raising=False)
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    return tmp_path


@pytest.fixture
def warned(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    log = MagicMock()
    monkeypatch.setattr(screenshot_refusal, "log", log)
    return log


def _macros(monkeypatch: pytest.MonkeyPatch, table: dict[str, list[dict[str, Any]]]) -> None:
    monkeypatch.setattr(execution, "load_macro", lambda name: {"actions": table[name]})


def _ledger_screenshot(session: MagicMock) -> None:
    """``BrowserSession.screenshot``'s ledger branch, as `core_page_mixin` takes it."""

    async def screenshot(path: Path) -> Path:
        ledger = getattr(session, SESSION_PRIVACY_LEDGER_ATTR)
        await safe_screenshot.ledger_screenshot(session, path, ledger.values)
        return path

    session.screenshot = screenshot


async def _failure(coro: Any) -> RuntimeError:
    with pytest.raises(RuntimeError) as raised:
        await coro
    return raised.value


def _everything_said(exc: RuntimeError, warned: MagicMock) -> str:
    """Every channel the refusal reaches: the raw error, both failure lines, the payload, the log."""
    payload = exc.args[0]
    return "\n".join(
        [
            str(exc),
            repr(exc),
            runner.redact_error(exc),
            sequence_steps.failure_line(payload),
            repr(payload),
            repr(warned.mock_calls),
        ]
    )


def _assert_says_nothing_of(text: str, value: str) -> None:
    assert value.lower() not in text.lower()
    assert AROUND.lower() not in text.lower()


@pytest.mark.asyncio
async def test_the_bmf_case_names_surface_tier_and_argument(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, warned: MagicMock
) -> None:
    """A credential signed in by one step, rendered re-cased by the app, refuses the next step's screenshot."""
    shot = str(tmp_path / "header.png")
    _macros(monkeypatch, {"bmf-login": [], "bmf-screenshot": [{"action": "screenshot", "path": shot}]})
    page = FakePage(snapshots=(_rendering(f"{AROUND} Admin"),))
    session = _session(page)
    _ledger_screenshot(session)

    await execution._run_macro_impl(
        session, "bmf-login", {"username": USERNAME}, slowmo_ms=0, credential_args=frozenset({"username"})
    )
    exc = await _failure(execution._run_macro_impl(session, "bmf-screenshot", None, slowmo_ms=0))

    payload = exc.args[0]
    assert payload["screenshot_refused"] == {
        "reasons": ["rendered text"],
        "stage": "before",
        "tiers": ["credential"],
        "args": ["username"],
    }
    assert runner.redact_error(exc) == (
        "macro bmf-screenshot failed at step 0 (screenshot): "
        "screenshot refused (rendered text; tier credential; argument username)"
    )
    assert "rendered text; tier credential; argument username" in sequence_steps.failure_line(payload)
    warned.warning.assert_called_once_with(
        "octowright.macro.screenshot_refused",
        reasons=["rendered text"],
        stage="before",
        tiers=["credential"],
        args=["username"],
    )
    _assert_says_nothing_of(_everything_said(exc, warned), USERNAME)
    assert not (tmp_path / "header.png").exists()


@pytest.mark.asyncio
async def test_an_identity_value_rendered_re_cased_names_its_tier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, warned: MagicMock
) -> None:
    monkeypatch.setenv(POLICY_ENV, "redact")
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, "all")
    _macros(monkeypatch, {"profile-shot": [{"action": "screenshot", "path": str(tmp_path / "p.png")}]})
    page = FakePage(snapshots=(_rendering(f"{AROUND} {EMAIL.upper()}"),))

    exc = await _failure(execution._run_macro_impl(_session(page), "profile-shot", {"email": EMAIL}, slowmo_ms=0))

    refused = exc.args[0]["screenshot_refused"]
    assert refused["reasons"] == ["rendered text"]
    assert refused["tiers"] == ["identity"]
    assert refused["args"] == ["email"]
    assert "tier identity; argument email" in runner.redact_error(exc)
    _assert_says_nothing_of(_everything_said(exc, warned), EMAIL)


@pytest.mark.asyncio
async def test_only_the_values_found_rendered_are_attributed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, warned: MagicMock
) -> None:
    """Two classified arguments, one rendered: the refusal names the one the page drew."""
    monkeypatch.setenv(POLICY_ENV, "redact")
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, "all")
    _macros(monkeypatch, {"two": [{"action": "screenshot", "path": str(tmp_path / "t.png")}]})
    page = FakePage(snapshots=(_rendering(f"{AROUND} {EMAIL}"),))
    args = {"email": EMAIL, "password": "B7-UNRENDERED-PASSWORD-91"}  # pragma: allowlist secret

    exc = await _failure(execution._run_macro_impl(_session(page), "two", args, slowmo_ms=0))

    refused = exc.args[0]["screenshot_refused"]
    assert refused["tiers"] == ["identity"]
    assert refused["args"] == ["email"]


@pytest.mark.asyncio
async def test_a_refusal_before_any_scan_names_why_the_screenshot_was_classified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, warned: MagicMock
) -> None:
    """The default ``refuse`` policy: no surface was read, so every held value is why it was classified."""
    _macros(monkeypatch, {"m": [{"action": "screenshot", "path": str(tmp_path / "m.png")}]})
    page = FakePage()

    exc = await _failure(
        execution._run_macro_impl(
            _session(page), "m", {"username": USERNAME}, slowmo_ms=0, credential_args=frozenset({"username"})
        )
    )

    refused = exc.args[0]["screenshot_refused"]
    assert refused == {"reasons": ["no privacy handler"], "tiers": ["credential"], "args": ["username"]}
    assert page.calls == []
    warned.warning.assert_called_once()
    _assert_says_nothing_of(_everything_said(exc, warned), USERNAME)


@pytest.mark.asyncio
async def test_a_value_with_no_known_argument_still_names_the_surface(tmp_path: Path, warned: MagicMock) -> None:
    """A direct call holds values with no recorded provenance: the surface is still named, nothing is guessed."""
    page = FakePage(snapshots=(_rendering(f"{AROUND} {USERNAME}"),))

    with pytest.raises(screenshot_refusal.ScreenshotRefused) as raised:
        await safe_screenshot.redacted_screenshot(
            _session(page), {"action": "screenshot", "path": str(tmp_path / "d.png")}, (USERNAME,)
        )

    assert raised.value.fields == {"reasons": ["rendered text"], "stage": "before", "tiers": [], "args": []}
    assert "(rendered text)" in str(raised.value)
    _assert_says_nothing_of(str(raised.value) + repr(warned.mock_calls), USERNAME)


def test_redact_error_without_a_refusal_is_unchanged() -> None:
    payload = {"macro": "m", "failed_at_step": 2, "failed_action": {"action": "click"}}
    assert runner.redact_error(RuntimeError(payload)) == "macro m failed at step 2 (click)"


def test_an_admissions_sources_are_recorded_but_stay_out_of_its_repr_and_equality() -> None:
    from octowright.macros.privacy import MacroArgPrivacy

    admission = MacroArgPrivacy.for_macro([], credential_args=frozenset({"username"})).admission({"username": USERNAME})
    assert [(item.path, item.tier) for item in admission.sources] == [("username", "credential")]
    assert "sources" not in repr(admission)
    assert admission == type(admission)(persistent=admission.persistent)


def test_a_typed_password_is_credential_tier_with_no_argument() -> None:
    from octowright.macros.privacy import admit_redacted_input
    from octowright.macros.privacy_ledger import held_provenance

    session = MagicMock()
    session.durable_text_scrubber = None
    admit_redacted_input(session, "typed-fixture-secret")  # pragma: allowlist secret
    assert held_provenance(session, ["typed-fixture-secret"]) == (["credential"], [])
