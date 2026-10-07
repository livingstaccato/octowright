# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exact digest output for the inputs a malformed macro or recording carries.

A digest is what ``macro_digest`` hands an agent instead of the whole macro or
recording, so its text is the contract. Each test compares the WHOLE digest:
the fallbacks for a missing name or action (``(unnamed)``, ``unknown``), the
``(none)`` parameter marker, the newline layout, and the rule that a field of
the wrong type is ignored rather than iterated or parsed.
"""

from __future__ import annotations

import json

import pytest

from octowright.artifacts.digest import digest_macro, digest_recording_text, sanitize_url, truncate_text


def _digest(summary: str) -> dict[str, object]:
    return {"summary": summary, "truncated": False, "source_size": len(summary), "cap": 4000}


# ---------------------------------------------------------------------------
# truncate_text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("max_chars", [0, -5])
def test_a_zero_or_negative_cap_keeps_nothing(max_chars: int) -> None:
    """The cap floors at zero, not one: a zero budget must not leak a character."""
    assert truncate_text("abc", max_chars=max_chars) == {
        "summary": "",
        "truncated": True,
        "source_size": 3,
        "cap": 0,
    }


# ---------------------------------------------------------------------------
# digest_macro
# ---------------------------------------------------------------------------


def test_a_macro_digest_names_its_fallbacks_exactly() -> None:
    """No name, no parameters, and an action row with no ``action`` key."""
    macro = {"actions": [{"selector": "#go"}, {"action": "click"}, "not-a-row"]}

    assert digest_macro(macro) == _digest("Macro (unnamed)\nparameters: (none)\nactions: 3\nclick: 1\nunknown: 1")


def test_fields_of_the_wrong_type_are_ignored_not_iterated() -> None:
    """A string is iterable; treating ``"abc"`` as a list would count three actions."""
    macro = {"name": "login", "actions": "abc", "parameters": "xy"}

    assert digest_macro(macro) == _digest("Macro login\nparameters: (none)\nactions: 0")


# ---------------------------------------------------------------------------
# digest_recording_text
# ---------------------------------------------------------------------------


def test_a_recording_digest_counts_events_and_keeps_only_url_origin_and_path() -> None:
    lines = [
        json.dumps({"action": "navigate", "url": "https://shop.example/a?token=secret#x"}),
        json.dumps({"ts": 1}),
        "not json",
        json.dumps({"action": "click", "url": 42}),
        "",
        json.dumps({"action": "navigate", "url": "https://shop.example/b"}),
    ]

    assert digest_recording_text("\n".join(lines)) == _digest(
        "events: 4\nmalformed: 1\n"
        "first_url: https://shop.example/a\nlast_url: https://shop.example/b\n"
        "click: 1\nnavigate: 2\nunknown: 1"
    )


def test_a_recording_with_no_url_reports_no_url_lines() -> None:
    assert digest_recording_text(json.dumps({"action": "click"})) == _digest("events: 1\nmalformed: 0\nclick: 1")


# ---------------------------------------------------------------------------
# sanitize_url
# ---------------------------------------------------------------------------


def test_an_empty_url_stays_empty() -> None:
    assert sanitize_url("") == ""


def test_a_relative_url_loses_its_query_and_fragment() -> None:
    assert sanitize_url("/orders/42?token=secret#frag") == "/orders/42"


def test_a_relative_url_whose_username_contains_an_at_sign_is_refused() -> None:
    """``///user@corp:pw@host`` is a credential-bearing URL to a browser.

    WHATWG (and ``urlsplit``'s own ``hostname``) split userinfo at the LAST
    ``@``. ``urlsplit`` collapses the leading slashes into a path, so this
    reaches ``_sanitize_relative_url``, whose userinfo check looked for ``:``
    only before the FIRST ``@`` -- ``/alice`` -- and returned the URL with
    ``zebrin4`` in it.
    """
    assert sanitize_url("///alice@corp.example:zebrin4@intranet/path") == "(invalid-url)"
