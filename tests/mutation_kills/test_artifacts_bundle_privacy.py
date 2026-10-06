# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The last check before an artifact bundle is written: the tripwire and the unresolved fallback."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from octowright.artifacts.bundle_privacy import BundlePrivacy, bundle_guard, guard_documents
from octowright.macros.privacy_ledger import RunPrivacyLedger, admit_redacted_input, session_privacy_ledger


class _Session:
    recorder: Any = None
    durable_text_scrubber: Any = None


def _guard(text: str, values: tuple[str, ...] = (), bounded: frozenset[str] = frozenset()) -> tuple[Any, dict]:
    guarded, flags = guard_documents({"summary.md": text}, BundlePrivacy(values=values, bounded=bounded))
    return guarded["summary.md"], flags


def test_a_longer_value_is_removed_before_a_shorter_one_inside_it() -> None:
    assert _guard("id abcdefgh", ("abcd", "abcdefgh")) == ("id <redacted>", {"privacy_tripwire": True})


def test_a_value_held_both_ways_is_matched_anywhere() -> None:
    assert _guard("xadmin1", ("admin1",), frozenset({"admin1"})) == ("x<redacted>", {"privacy_tripwire": True})


def test_a_bounded_only_value_inside_an_identifier_is_left() -> None:
    assert _guard("xadmin1 admin1", (), frozenset({"admin1"})) == ("xadmin1 <redacted>", {"privacy_tripwire": True})


def test_a_four_character_value_matches_inside_a_word() -> None:
    assert _guard("xabcd", ("abcd",)) == ("x<redacted>", {"privacy_tripwire": True})


def test_a_short_value_matches_only_on_a_boundary() -> None:
    assert _guard("x abc yabc", ("abc",)) == ("x <redacted> yabc", {"privacy_tripwire": True})


def test_text_the_replacement_reintroduces_is_replaced_whole() -> None:
    # The marker itself contains "acted", so one pass cannot clear it.
    assert _guard("foo acted", ("acted",)) == ("<redacted>", {"privacy_tripwire": True})


def test_a_check_that_cannot_run_marks_the_bundle_unresolved(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    privacy = BundlePrivacy(values=(5,))  # type: ignore[arg-type]

    guarded, flags = guard_documents({"result.json": {"password": "x", "ok": 1}}, privacy)

    assert guarded == {"result.json": {"password": "<redacted>", "ok": 1}}
    assert flags == {"privacy_unresolved": True}
    assert privacy.flags == {"privacy_unresolved": True}
    messages = [record.getMessage() for record in caplog.records]
    assert any(" octowright.artifacts.privacy_check_failed [" in m and "error_type=TypeError" in m for m in messages), (
        messages
    )


def test_the_tripwire_log_names_documents_never_values(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)

    guard_documents({"summary.md": "pw zebrin4-secret", "result.json": {}}, BundlePrivacy(values=("zebrin4-secret",)))

    messages = [record.getMessage() for record in caplog.records]
    tripwire = [m for m in messages if " octowright.artifacts.privacy_tripwire [" in m]
    assert len(tripwire) == 1, messages
    assert "documents=['result.json', 'summary.md']" in tripwire[0]
    assert "zebrin4" not in tripwire[0]


def test_bundle_guard_matches_typed_passwords_as_identifiers_only() -> None:
    session = _Session()
    admit_redacted_input(session, "admin")
    session_privacy_ledger(session).add(["anywhere-value"])
    run = RunPrivacyLedger(session)
    run.add(["run-value"])

    guard = bundle_guard(session, run)

    assert guard.values == ("anywhere-value", "run-value")
    assert guard.bounded == frozenset({"admin"})
    assert guard.unresolved is True
