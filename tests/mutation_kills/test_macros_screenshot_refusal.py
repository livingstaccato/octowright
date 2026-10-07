# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exact text and fields of a refused classified screenshot, and how a refusal is found in a cause chain.

`test_macro_screenshot_refusal_reason.py` proves a refusal never names the value;
this pins what it does say: the message an operator reads, the ``screenshot_refused``
payload, and the failure-line summary, each to the character.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

from octowright.macros.privacy_ledger import SESSION_PRIVACY_LEDGER_ATTR, SessionPrivacyLedger
from octowright.macros.screenshot_refusal import FIELD, Refusals, ScreenshotRefused, refusal_fields, summary
from octowright.macros.scrub_engine import REDACTED


def _session(*sources: tuple[str, str, str]) -> SimpleNamespace:
    ledger = SessionPrivacyLedger([value for value, _, _ in sources])
    ledger.note_sources(sources)
    return SimpleNamespace(**{SESSION_PRIVACY_LEDGER_ATTR: ledger})


def test_summary_lists_every_reason_tier_and_argument() -> None:
    fields = {
        "reasons": ["rendered text", "form value"],
        "tiers": ["credential", "identity"],
        "args": ["login.user", "login.password"],
    }

    assert summary(fields) == (
        "screenshot refused (rendered text, form value; tier credential, identity; argument login.user, login.password)"
    )


def test_summary_without_anything_to_say_is_the_bare_phrase() -> None:
    assert summary({}) == "screenshot refused"
    assert summary({"reasons": [], "tiers": [], "args": []}) == "screenshot refused"


def test_a_plain_refusal_names_tier_and_argument_but_not_its_reason_in_the_message() -> None:
    session = _session(("zebrin4", "credential", "login.password"), ("quokka", "identity", "login.user"))

    refused = Refusals(session, ("zebrin4", "quokka")).refuse("a head", "no privacy handler", stage="before")

    assert isinstance(refused, ScreenshotRefused)
    assert str(refused) == (
        "a head (tier credential, identity; argument login.password, login.user); screenshot refused"
    )
    assert refused.fields == {
        "reasons": ["no privacy handler"],
        "stage": "before",
        "tiers": ["credential", "identity"],
        "args": ["login.password", "login.user"],
    }


def test_a_refusal_with_no_provenance_ends_with_the_bare_phrase() -> None:
    refused = Refusals(SimpleNamespace(), ("zebrin4",)).refuse("a head", "handler refused")

    assert str(refused) == "a head; screenshot refused"
    assert refused.fields == {"reasons": ["handler refused"], "tiers": [], "args": []}


def test_an_argument_path_that_spells_a_held_value_is_shown_as_the_marker() -> None:
    session = _session(("zebrin4", "credential", "Zebrin4"), ("quokka", "identity", "login.user"))

    refused = Refusals(session, ("zebrin4", "quokka")).refuse("a head", "handler refused")

    assert refused.fields["args"] == [REDACTED, "login.user"]


def test_only_non_empty_string_values_are_held() -> None:
    session = _session(("zebrin4", "credential", "login.password"))
    held: list[Any] = ["", None, 5, "zebrin4"]

    refused = Refusals(session, held).refuse("a head", "handler refused")

    assert refused.fields == {"reasons": ["handler refused"], "tiers": ["credential"], "args": ["login.password"]}


def _refusal() -> ScreenshotRefused:
    return ScreenshotRefused("refused", {"reasons": ["page changed"], "stage": "after"})


def test_a_refusal_is_found_as_an_explicit_cause() -> None:
    outer = RuntimeError("step failed")
    outer.__cause__ = _refusal()

    assert refusal_fields(outer) == {FIELD: {"reasons": ["page changed"], "stage": "after"}}


def test_a_refusal_is_found_as_an_implicit_context_without_a_cause() -> None:
    try:
        try:
            raise _refusal()
        except ScreenshotRefused:
            raise RuntimeError("while handling") from None
    except RuntimeError as exc:
        # ``from None`` clears the cause and suppresses the context's display, but keeps it.
        assert exc.__cause__ is None
        assert isinstance(exc.__context__, ScreenshotRefused)
        found = refusal_fields(exc)

    assert found == {FIELD: {"reasons": ["page changed"], "stage": "after"}}


def test_a_cyclic_chain_without_a_refusal_ends() -> None:
    first, second = RuntimeError("a"), RuntimeError("b")
    first.__context__ = second
    second.__context__ = first
    result: list[dict[str, Any]] = []

    worker = threading.Thread(target=lambda: result.append(refusal_fields(first)), daemon=True)
    worker.start()
    worker.join(timeout=2)

    assert not worker.is_alive(), "refusal_fields looped on a cyclic exception chain"
    assert result == [{}]


def test_no_exception_has_no_refusal() -> None:
    assert refusal_fields(None) == {}
    assert refusal_fields(RuntimeError("plain")) == {}
