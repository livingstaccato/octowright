# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A password typed into a ``type=password`` field is scrubbed as a whole token.

Every such value joins the session ledger so a page echo of it is scrubbed
from every later row -- but a value of four or more characters was replaced
anywhere, so a test password like ``admin`` rewrote ``administrator`` to
``<redacted>istrator`` and ``#admin-menu`` to ``#<redacted>-menu``, and a
macro saved from that recording replayed broken selectors. Typed-password
entries now match only where they are not embedded in a longer identifier
(letters, digits, ``_`` and ``-``). A macro's own classified values keep
matching anywhere; see ``test_macro_privacy_scrub_scope``.
"""

from __future__ import annotations

from typing import Any

from octowright.macros import privacy

TYPED = "admin"  # pragma: allowlist secret -- a fixture, never a real credential
PUNCTUATED = "!Tr0ub4dor3"  # pragma: allowlist secret -- a fixture


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def record(self, action: str, **fields: Any) -> None:
        self.rows.append((action, fields))

    def record_control(self, action: str, **fields: Any) -> None:
        self.rows.append((action, fields))


class _Session:
    durable_text_scrubber: Any = None

    def __init__(self) -> None:
        self.recorder: Any = _Recorder()


def _typed(value: str) -> tuple[_Session, _Recorder]:
    session = _Session()
    inner = session.recorder
    privacy.admit_redacted_input(session, value)
    return session, inner


def test_a_typed_password_inside_a_longer_word_is_left_alone() -> None:
    session, inner = _typed(TYPED)

    session.recorder.record("click", selector="#admin-menu", text="administrator", role_name="admin_panel")

    assert inner.rows[-1][1] == {"selector": "#admin-menu", "text": "administrator", "role_name": "admin_panel"}


def test_a_typed_password_echoed_as_its_own_token_is_scrubbed() -> None:
    session, inner = _typed(TYPED)

    echoed = '{"password":"admin"}'  # pragma: allowlist secret -- a fixture
    session.recorder.record("console", text="login pw=admin ok", body=echoed)

    fields = inner.rows[-1][1]
    assert fields["text"] == "login pw=<redacted> ok"
    assert fields["body"] == '{"password":"<redacted>"}'


def test_a_typed_password_is_scrubbed_in_its_encoded_spellings_too() -> None:
    session, inner = _typed("p@ss w0rd")  # pragma: allowlist secret

    session.recorder.record("request", url="https://x.test/?p=p%40ss%20w0rd&n=1")

    assert inner.rows[-1][1]["url"] == "https://x.test/?p=<redacted>&n=1"


def test_a_punctuated_edge_needs_no_boundary_on_that_side() -> None:
    """The boundary guards an identifier edge; a value that starts with
    punctuation cannot be the tail of an identifier, so it matches after one."""
    session, inner = _typed(PUNCTUATED)

    session.recorder.record("console", text=f"x{PUNCTUATED} and {PUNCTUATED}y")

    assert inner.rows[-1][1]["text"] == f"x<redacted> and {PUNCTUATED}y"


def test_the_markdown_scrubber_uses_the_same_bounds() -> None:
    session, _inner = _typed(TYPED)

    scrub = session.durable_text_scrubber
    assert scrub is not None
    assert scrub("Hello administrator, your password is admin.") == (
        "Hello administrator, your password is <redacted>."
    )


def test_a_value_also_admitted_by_a_macro_keeps_matching_anywhere() -> None:
    """The stronger scrub wins: a macro classified the same value as a credential."""
    session, inner = _typed(TYPED)
    privacy.install_sensitive_recorder(session, [TYPED])

    session.recorder.record("click", text="administrator")

    assert inner.rows[-1][1]["text"] == "<redacted>istrator"


def test_macro_values_still_match_inside_longer_words() -> None:
    """Unchanged for a macro's classified values -- pinned elsewhere, restated here
    so the typed-input change visibly did not touch them."""
    assert privacy.scrub_sensitive_values("abcd abcde", ("abcd",)) == "<redacted> <redacted>e"


def test_merging_the_session_ledger_keeps_a_typed_password_word_bounded() -> None:
    """``with_session_ledger`` merges ledgers: the run's values still match anywhere, typed ones as tokens."""
    session, _inner = _typed(TYPED)

    merged = privacy.with_session_ledger(session, ["tok-3f9a"])

    assert merged.word_bounded == frozenset({TYPED})
    assert merged.scrub("Administrator admin tok-3f9a-x") == "Administrator <redacted> <redacted>-x"


def test_a_value_the_run_also_holds_is_scrubbed_anywhere() -> None:
    """A run's own classified value is the stronger claim, whichever ledger held it first."""
    session, _inner = _typed(TYPED)

    assert privacy.with_session_ledger(session, [TYPED]).word_bounded == frozenset()


async def test_a_macro_failure_payload_keeps_administrator(monkeypatch: Any) -> None:
    """The failure scrub reads the merged ledger, so a typed ``admin`` leaves ``#admin-menu`` intact."""
    from unittest.mock import AsyncMock, MagicMock

    import pytest

    from octowright.macros import execution

    session = MagicMock()
    session.durable_text_scrubber = None
    privacy.admit_redacted_input(session, TYPED)
    session.click = AsyncMock(side_effect=RuntimeError("timed out waiting for #admin-menu (admin)"))
    session.diagnostic_bundle = AsyncMock(return_value={})
    monkeypatch.setattr(
        execution, "load_macro", lambda _n: {"actions": [{"action": "click", "selector": "#admin-menu"}]}
    )
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError) as raised:
        await execution._run_macro_impl(session, "m", {"token": "fixture-run-token"}, slowmo_ms=0)
    original = raised.value.args[0]["original"]
    assert "#admin-menu" in original and "(admin)" not in original
