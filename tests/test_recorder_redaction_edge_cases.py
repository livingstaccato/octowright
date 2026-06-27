# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Edge-case coverage for ``OCTOWRIGHT_REDACT_INPUTS`` redaction policy.

``tests/test_session_page_mixin_branches.py::TestInputRedaction`` already
pins the happy paths (``type=password`` redacted, ``type=text`` not, mode
``off`` disables, mode ``all`` redacts everything, evaluate-failure
fail-soft). This file fills in the gaps that the recent autocomplete-
extended ``_is_password_input`` rewrite opened up:

- ``type=text`` paired with ``autocomplete=current-password / new-password
  / one-time-code`` (the SPA-custom-password-field pattern).
- ``type=text`` with an unrelated autocomplete token (must NOT redact).
- Custom elements where ``type`` is empty but ``autocomplete`` is set —
  the IDL property is unavailable, only the attribute is.
- Legacy ``evaluate`` return shape (bare string) — back-compat contract.
- ``evaluate`` raising — fail-CLOSED to the redacted placeholder, and the
  debug log records both the selector and the error.
- Both ``type_text`` and ``fill`` honor the same policy.
- Multiple selectors on one form are each evaluated independently.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS, REDACTED_INPUT_PLACEHOLDER
from octowright.session.core_page_mixin import SessionPageMixin

REDACT_INPUTS_ENV = "OCTOWRIGHT_REDACT_INPUTS"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _locator_with_input_info(evaluate_return: Any, *, raises: bool = False) -> MagicMock:
    """Build a locator(...) mock whose .first.evaluate returns evaluate_return.

    If ``raises`` is True, .first.evaluate raises RuntimeError instead.
    Also satisfies the aria_snapshot() call from _resolve_semantic_metadata.
    """
    locator = MagicMock()
    locator.aria_snapshot = AsyncMock(return_value="")
    first = MagicMock()
    if raises:
        first.evaluate = AsyncMock(side_effect=RuntimeError("locator detached"))
    else:
        first.evaluate = AsyncMock(return_value=evaluate_return)
    locator.first = first
    return locator


def _make_redaction_subject(evaluate_return: Any, *, raises: bool = False) -> SessionPageMixin:
    """Reusable subject builder. Stubs _target() to return a target whose
    locator(selector) returns a uniform mock regardless of selector."""
    subj = SessionPageMixin.__new__(SessionPageMixin)
    subj._last_mcp_navigation = None
    subj.page = MagicMock()
    subj.pages = [subj.page]
    subj.recorder = MagicMock()
    subj.recorder.record = MagicMock()

    target = MagicMock()
    target.type = AsyncMock()
    target.fill = AsyncMock()
    target.locator = MagicMock(return_value=_locator_with_input_info(evaluate_return, raises=raises))
    subj._target = lambda: target  # type: ignore[attr-defined]
    return subj


# ─── autocomplete-driven redaction of type=text fields ─────────────────────


class TestAutocompleteRedaction:
    """SPAs implement password fields as <input type=text autocomplete=...>;
    the autocomplete token is the only DOM signal that the value is
    credential-bearing. _is_password_input must catch all three
    well-known credential tokens.
    """

    @pytest.mark.anyio
    async def test_redacts_text_field_with_autocomplete_current_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """type=text + autocomplete=current-password → redacted."""
        monkeypatch.delenv(REDACT_INPUTS_ENV, raising=False)
        subj = _make_redaction_subject({"type": "text", "ac": "current-password"})
        await subj.fill("#cred", "hunter2")
        target = subj._target()
        # Page still receives the literal — typing must work.
        target.fill.assert_awaited_once_with("#cred", "hunter2", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        call = subj.recorder.record.call_args
        assert call.args == ("fill",)
        assert call.kwargs["value"] == REDACTED_INPUT_PLACEHOLDER
        assert call.kwargs["value"] != "hunter2"

    @pytest.mark.anyio
    async def test_redacts_text_field_with_autocomplete_new_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """type=text + autocomplete=new-password → redacted (signup flow)."""
        monkeypatch.delenv(REDACT_INPUTS_ENV, raising=False)
        subj = _make_redaction_subject({"type": "text", "ac": "new-password"})
        await subj.fill("#new-pw", "fresh-secret")
        target = subj._target()
        target.fill.assert_awaited_once_with("#new-pw", "fresh-secret", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        call = subj.recorder.record.call_args
        assert call.kwargs["value"] == REDACTED_INPUT_PLACEHOLDER

    @pytest.mark.anyio
    async def test_redacts_text_field_with_autocomplete_one_time_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """type=text + autocomplete=one-time-code → redacted (MFA / OTP)."""
        monkeypatch.delenv(REDACT_INPUTS_ENV, raising=False)
        subj = _make_redaction_subject({"type": "text", "ac": "one-time-code"})
        await subj.fill("#otp", "123456")
        target = subj._target()
        target.fill.assert_awaited_once_with("#otp", "123456", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        call = subj.recorder.record.call_args
        assert call.kwargs["value"] == REDACTED_INPUT_PLACEHOLDER

    @pytest.mark.anyio
    async def test_does_not_redact_text_field_with_unrelated_autocomplete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """type=text + autocomplete=username → NOT redacted. Only the
        documented credential tokens (current-password / new-password /
        one-time-code) trigger redaction; ``username`` carries no secret."""
        monkeypatch.delenv(REDACT_INPUTS_ENV, raising=False)
        subj = _make_redaction_subject({"type": "text", "ac": "username"})
        await subj.fill("#user", "alice@octowright.test")
        call = subj.recorder.record.call_args
        assert call.kwargs["value"] == "alice@octowright.test"
        assert call.kwargs["value"] != REDACTED_INPUT_PLACEHOLDER

    @pytest.mark.anyio
    async def test_redacts_custom_element_with_attribute_only_autocomplete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Custom element (no IDL ``type``) with the attribute set is the
        whole reason the JS reads ``getAttribute('autocomplete')`` in
        addition to ``el.autocomplete``. Empty ``type`` + ``ac=current-password``
        must still redact."""
        monkeypatch.delenv(REDACT_INPUTS_ENV, raising=False)
        subj = _make_redaction_subject({"type": "", "ac": "current-password"})
        await subj.fill("custom-password", "ce-secret")
        target = subj._target()
        target.fill.assert_awaited_once_with("custom-password", "ce-secret", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        call = subj.recorder.record.call_args
        assert call.kwargs["value"] == REDACTED_INPUT_PLACEHOLDER


# ─── legacy evaluate-string return shape (back-compat) ─────────────────────


class TestLegacyEvaluateReturn:
    """Older callers / older test mocks may stub ``evaluate`` to return a
    bare string (the original behavior before the {type, ac} dict shape).
    ``_is_password_input`` MUST still treat ``"password"`` as redactable
    and any other string as non-credential, so existing macros and external
    consumers of the policy don't silently regress."""

    @pytest.mark.anyio
    async def test_legacy_evaluate_string_return_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bare ``"password"`` → redact; bare ``"text"`` → don't."""
        monkeypatch.delenv(REDACT_INPUTS_ENV, raising=False)

        # Legacy shape: evaluate returns just the type string.
        subj_pw = _make_redaction_subject("password")
        await subj_pw.fill("#pw", "hunter2")
        assert subj_pw.recorder.record.call_args.kwargs["value"] == REDACTED_INPUT_PLACEHOLDER

        subj_text = _make_redaction_subject("text")
        await subj_text.fill("#name", "alice")
        assert subj_text.recorder.record.call_args.kwargs["value"] == "alice"


# ─── fail-closed contract when evaluate raises ─────────────────────────────


class TestEvaluateFailsClosed:
    """A flaky DOM lookup (element detached, page navigated mid-call,
    Playwright internal error) MUST fall back to redacting. Anything
    else would risk writing a cleartext password into the JSONL recording
    because the policy couldn't read the element."""

    @pytest.mark.anyio
    async def test_evaluate_raises_fails_closed_to_redacted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """evaluate() raises → recorder gets the placeholder, debug log
        carries both selector and stringified error.

        Uses the same module-``log`` patching pattern as
        ``tests/test_open_url.py::_DebugCapture`` because provide.telemetry
        routes through stdlib logging but caplog's level filter is unreliable
        across the GH-Actions runner profile."""
        monkeypatch.delenv(REDACT_INPUTS_ENV, raising=False)

        from octowright.session import core_page_mixin as _mod

        events: list[tuple[str, dict[str, Any]]] = []

        class _Cap:
            def debug(self, event: str, **kw: Any) -> None:
                events.append((event, kw))

            def warning(self, event: str, **kw: Any) -> None:
                events.append((event, kw))

            def info(self, event: str, **kw: Any) -> None:
                events.append((event, kw))

        monkeypatch.setattr(_mod, "log", _Cap())

        subj = _make_redaction_subject(None, raises=True)
        await subj.fill("#mystery", "secret-value")

        # Fail-closed: redacted recording.
        call = subj.recorder.record.call_args
        assert call.kwargs["value"] == REDACTED_INPUT_PLACEHOLDER
        # Debug log carries the selector and the stringified error so an
        # operator can diagnose why the lookup failed.
        names = [name for name, _ in events]
        assert "core_page_mixin.password_lookup_failed" in names
        _, kw = next((e, k) for e, k in events if e == "core_page_mixin.password_lookup_failed")
        assert kw.get("selector") == "#mystery"
        assert "locator detached" in str(kw.get("error", ""))


# ─── mode boundaries: off / all ────────────────────────────────────────────


class TestRedactionModes:
    @pytest.mark.anyio
    async def test_mode_off_does_not_redact_even_password_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OCTOWRIGHT_REDACT_INPUTS=off is the operator's explicit opt-out;
        even a clear-cut <input type=password> writes the literal into the
        recording. Documents the 'operator chose this, accept the leak'
        contract — the linter still catches it at macro save-time."""
        monkeypatch.setenv(REDACT_INPUTS_ENV, "off")
        subj = _make_redaction_subject({"type": "password", "ac": ""})
        await subj.fill("#pw", "hunter2")
        call = subj.recorder.record.call_args
        assert call.kwargs["value"] == "hunter2"
        assert call.kwargs["value"] != REDACTED_INPUT_PLACEHOLDER

    @pytest.mark.anyio
    async def test_mode_all_redacts_every_typed_value_regardless_of_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OCTOWRIGHT_REDACT_INPUTS=all blanket-redacts; the policy doesn't
        even consult the element type. Verified by stubbing a plain
        <input type=text> with no autocomplete — would NOT redact under
        ``passwords`` mode, but MUST redact under ``all``."""
        monkeypatch.setenv(REDACT_INPUTS_ENV, "all")
        subj = _make_redaction_subject({"type": "text", "ac": ""})
        await subj.fill("#name", "alice")
        target = subj._target()
        # Page still receives the literal.
        target.fill.assert_awaited_once_with("#name", "alice", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        # Recorder sees the placeholder.
        call = subj.recorder.record.call_args
        assert call.kwargs["value"] == REDACTED_INPUT_PLACEHOLDER


# ─── policy applies to type_text not just fill ─────────────────────────────


class TestTypeTextHonorsPolicy:
    @pytest.mark.anyio
    async def test_redaction_applies_to_type_text_not_just_fill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The same policy must scrub the ``text=`` kwarg on the recorder's
        ``type`` event, not only the ``value=`` kwarg on ``fill``."""
        monkeypatch.delenv(REDACT_INPUTS_ENV, raising=False)
        subj = _make_redaction_subject({"type": "text", "ac": "current-password"})
        await subj.type_text("#cred", "hunter2", None)
        target = subj._target()
        # Page receives the literal at the real keystroke rate.
        target.type.assert_awaited_once_with("#cred", "hunter2", delay=0, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        # Recorder gets the placeholder under text=.
        call = subj.recorder.record.call_args
        assert call.args == ("type",)
        assert call.kwargs["text"] == REDACTED_INPUT_PLACEHOLDER
        assert call.kwargs["text"] != "hunter2"


# ─── selector-less sink redaction (press_key / evaluate / select_option) ───


class TestSinkRedaction:
    """``fill``/``type`` can inspect the target element to decide if a value is
    a credential. ``press_key``, ``evaluate``, and ``select_option`` carry no
    inspectable field, so the *only* coherent policy is: redact under the
    blanket ``all`` mode, leave raw under ``off``/``passwords``. The page must
    always receive the real value. These three sinks previously recorded raw in
    every mode — a char-by-char password (`press_key`), a credential baked into
    a JS string (`evaluate`), or a secret dropdown value (`select_option`)
    landed cleartext in the JSONL even under ``all``."""

    @pytest.mark.anyio
    async def test_press_key_redacted_under_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(REDACT_INPUTS_ENV, "all")
        subj = _make_redaction_subject({"type": "text", "ac": ""})
        subj.page.keyboard.press = AsyncMock()
        await subj.press_key("a")
        # Page still receives the real key.
        subj.page.keyboard.press.assert_awaited_once_with("a")
        call = subj.recorder.record.call_args
        assert call.args == ("press_key",)
        assert call.kwargs["key"] == REDACTED_INPUT_PLACEHOLDER

    @pytest.mark.anyio
    async def test_press_key_raw_under_passwords_and_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for mode in ("passwords", "off"):
            monkeypatch.setenv(REDACT_INPUTS_ENV, mode)
            subj = _make_redaction_subject({"type": "text", "ac": ""})
            subj.page.keyboard.press = AsyncMock()
            await subj.press_key("Enter")
            assert subj.recorder.record.call_args.kwargs["key"] == "Enter", mode

    @pytest.mark.anyio
    async def test_evaluate_expression_redacted_under_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(REDACT_INPUTS_ENV, "all")
        subj = _make_redaction_subject({"type": "text", "ac": ""})
        subj._target().evaluate = AsyncMock(return_value=None)
        await subj.evaluate("document.querySelector('#pw').value='S3cret!'")
        # Page still runs the real script.
        subj._target().evaluate.assert_awaited_once_with("document.querySelector('#pw').value='S3cret!'")
        call = subj.recorder.record.call_args
        assert call.args == ("evaluate",)
        assert call.kwargs["expression"] == REDACTED_INPUT_PLACEHOLDER

    @pytest.mark.anyio
    async def test_evaluate_expression_raw_under_passwords(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(REDACT_INPUTS_ENV, "passwords")
        subj = _make_redaction_subject({"type": "text", "ac": ""})
        subj._target().evaluate = AsyncMock(return_value=42)
        await subj.evaluate("1+41")
        assert subj.recorder.record.call_args.kwargs["expression"] == "1+41"

    @pytest.mark.anyio
    async def test_select_option_value_label_redacted_under_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.session.core_ops_mixin import SessionOpsMixin

        monkeypatch.setenv(REDACT_INPUTS_ENV, "all")
        subj = SessionOpsMixin.__new__(SessionOpsMixin)
        subj.recorder = MagicMock()
        subj.recorder.record = MagicMock()
        target = MagicMock()
        target.select_option = AsyncMock(return_value=["secret-val"])
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.select_option("#sel", value="secret-val", label="Secret Label", index=2)
        # Page still receives the real value.
        assert target.select_option.await_args.kwargs["value"] == "secret-val"
        call = subj.recorder.record.call_args
        assert call.args == ("select_option",)
        assert call.kwargs["value"] == REDACTED_INPUT_PLACEHOLDER
        assert call.kwargs["label"] == REDACTED_INPUT_PLACEHOLDER
        assert call.kwargs["index"] == 2  # positional int, not a secret — untouched

    @pytest.mark.anyio
    async def test_select_option_raw_under_passwords(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.session.core_ops_mixin import SessionOpsMixin

        monkeypatch.setenv(REDACT_INPUTS_ENV, "passwords")
        subj = SessionOpsMixin.__new__(SessionOpsMixin)
        subj.recorder = MagicMock()
        subj.recorder.record = MagicMock()
        target = MagicMock()
        target.select_option = AsyncMock(return_value=["us"])
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.select_option("#country", value="us")
        assert subj.recorder.record.call_args.kwargs["value"] == "us"


# ─── per-selector evaluation independence ──────────────────────────────────


class TestPerSelectorEvaluation:
    @pytest.mark.anyio
    async def test_multiple_fields_on_same_form_each_evaluated_independently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A login form has both a username (non-redacted) and a password
        (redacted) field. Each fill() call must evaluate the element
        independently — there's no caching that could leak the policy
        from one selector to the next."""
        monkeypatch.delenv(REDACT_INPUTS_ENV, raising=False)

        subj = SessionPageMixin.__new__(SessionPageMixin)
        subj._last_mcp_navigation = None
        subj.page = MagicMock()
        subj.pages = [subj.page]
        subj.recorder = MagicMock()
        subj.recorder.record = MagicMock()

        # Per-selector locator behavior: the same target.locator(selector)
        # returns different .first.evaluate results depending on which
        # selector was requested.
        per_selector = {
            "#email": _locator_with_input_info({"type": "email", "ac": ""}),
            "#pw": _locator_with_input_info({"type": "password", "ac": ""}),
        }
        target = MagicMock()
        target.fill = AsyncMock()
        target.locator = MagicMock(side_effect=lambda sel: per_selector[sel])
        subj._target = lambda: target  # type: ignore[attr-defined]

        await subj.fill("#email", "alice@octowright.test")
        await subj.fill("#pw", "hunter2")

        calls = subj.recorder.record.call_args_list
        # First call: email — literal value.
        assert calls[0].args == ("fill",)
        assert calls[0].kwargs["value"] == "alice@octowright.test"
        # Second call: password — placeholder.
        assert calls[1].args == ("fill",)
        assert calls[1].kwargs["value"] == REDACTED_INPUT_PLACEHOLDER
        # And the page itself always saw the real value.
        assert target.fill.await_args_list[0].args == ("#email", "alice@octowright.test")
        assert target.fill.await_args_list[1].args == ("#pw", "hunter2")
