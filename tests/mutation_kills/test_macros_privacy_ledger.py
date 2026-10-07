# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The scrub ledgers' edges: what they admit, how they report provenance, and when they refuse."""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.privacy import ScrubAdmission
from octowright.macros.privacy_ledger import (
    PrivacyLedger,
    RunPrivacyLedger,
    SensitiveRecorder,
    SessionPrivacyLedger,
    admit_call_privacy,
    scrub_with_session,
    session_privacy_ledger,
)
from octowright.macros.scrub_capacity import SCRUB_MAX_VALUES_ENV
from octowright.request_errors import InvalidRequestError


class _Session:
    recorder: Any = None
    durable_text_scrubber: Any = None


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, dict[str, Any]]] = []

    def record(self, action: str, **fields: Any) -> None:
        self.rows.append(("record", action, fields))

    def record_control(self, action: str, **fields: Any) -> None:
        self.rows.append(("record_control", action, fields))


@pytest.fixture(autouse=True)
def _default_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SCRUB_MAX_VALUES_ENV, raising=False)


def test_only_non_empty_strings_are_admitted() -> None:
    ledger = PrivacyLedger([5, "", None, "abc1", True])  # type: ignore[list-item]

    assert ledger.values == ("abc1",)


def test_note_sources_skips_non_strings_and_empties() -> None:
    ledger = SessionPrivacyLedger(["abc1"])
    ledger.note_sources([(5, "credential", "x"), ("", "credential", "y"), ("abc1", "identity", "email")])  # type: ignore[list-item]

    assert ledger.provenance(["abc1", 5, ""]) == (["identity"], ["email"])  # type: ignore[list-item]


def test_provenance_orders_tiers_strongest_first_and_unknown_last() -> None:
    ledger = SessionPrivacyLedger()
    ledger.note_sources(
        [
            ("v1", "contextual", "peer"),
            ("v2", "identity", "email"),
            ("v3", "credential", "password"),
            ("v4", "weird", ""),
        ]
    )

    assert ledger.provenance(["v1", "v2", "v3", "v4"]) == (
        ["credential", "identity", "contextual", "weird"],
        ["email", "password", "peer"],
    )


def test_a_fresh_session_ledger_is_not_saturated() -> None:
    assert SessionPrivacyLedger().saturated is False


def test_a_zero_cap_refuses_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_MAX_VALUES_ENV, "0")
    ledger = SessionPrivacyLedger(["held"])

    assert ledger.refuse_if_full(["new-value"]) is None


def test_refuse_if_full_ignores_non_strings_when_counting_new_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_MAX_VALUES_ENV, "2")
    ledger = SessionPrivacyLedger(["held"])

    assert ledger.refuse_if_full([5, None, ""]) is None  # type: ignore[list-item]
    with pytest.raises(InvalidRequestError):
        ledger.refuse_if_full(["new-1", "new-2"])


def test_run_scoped_values_after_the_scope_closed_join_the_session() -> None:
    ledger = SessionPrivacyLedger()
    token = ledger.open_run_scope()
    ledger.add_run_scoped(token, ["scoped-1", 7, ""])  # type: ignore[list-item]
    assert ledger.values == ("scoped-1",)

    ledger.close_run_scope(token)
    assert ledger.values == ()
    ledger.close_run_scope(token)  # closing twice is harmless

    ledger.add_run_scoped(token, ["late-value"])
    assert ledger.values == ("late-value",)
    assert ledger.persistent_count == 1


def test_closing_an_unknown_scope_is_harmless() -> None:
    ledger = SessionPrivacyLedger(["held"])

    ledger.close_run_scope(object())

    assert ledger.values == ("held",)


def test_record_control_is_scrubbed_and_keeps_its_action() -> None:
    inner = _Recorder()
    recorder = SensitiveRecorder(inner, PrivacyLedger(["zebrin4pw"]))

    recorder.record_control("macro_start", note="pw zebrin4pw", n=1)
    recorder.record("fill", value="zebrin4pw")

    assert inner.rows == [
        ("record_control", "macro_start", {"note": "pw <redacted>", "n": 1}),
        ("record", "fill", {"value": "<redacted>"}),
    ]


def test_scrub_with_session_applies_the_session_ledger_after_the_runs() -> None:
    session = _Session()
    session_privacy_ledger(session).add(["session-secret"])

    assert scrub_with_session(session, "a session-secret b run-secret", PrivacyLedger(["run-secret"])) == (
        "a <redacted> b <redacted>"
    )
    assert scrub_with_session(_Session(), "a session-secret") == "a session-secret"


def test_an_empty_run_reports_no_exemptions_and_no_warnings() -> None:
    ledger = RunPrivacyLedger(_Session())

    assert ledger.exempt_fields() == {}
    assert ledger.warning_fields() == {}


def test_a_nested_call_outside_a_run_is_refused_by_a_full_session_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_MAX_VALUES_ENV, "1")
    session = _Session()
    session_privacy_ledger(session).add(["held"])

    with pytest.raises(InvalidRequestError):
        admit_call_privacy(session, PrivacyLedger(), "child", ScrubAdmission(persistent=("new-credential",)))

    assert session_privacy_ledger(session).values == ("held",)


def test_re_adding_held_values_keeps_the_built_scrubber() -> None:
    """An add that changes nothing must not rebuild the scrubber every live buffer calls per message."""
    ledger = PrivacyLedger(["held-anywhere"])
    ledger.add(["held-bounded"], word_bounded=True)
    built = ledger._text_scrubber()

    ledger.add(["held-anywhere"])
    ledger.add(["held-bounded"], word_bounded=True)

    assert ledger._text_scrubber() is built
    assert ledger.scrub("x held-anywhere y") == "x <redacted> y"


def test_re_adding_a_scopes_own_values_keeps_the_built_scrubber() -> None:
    ledger = SessionPrivacyLedger()
    token = ledger.open_run_scope()
    ledger.add_run_scoped(token, ["scoped-held"])
    built = ledger._text_scrubber()

    ledger.add_run_scoped(token, ["scoped-held"])

    assert ledger._text_scrubber() is built
    assert ledger.values == ("scoped-held",)
