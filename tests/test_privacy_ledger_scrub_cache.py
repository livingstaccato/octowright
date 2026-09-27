# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The session ledger's live scrub is cached per ledger state, and its output is unchanged.

``live_scrubbed`` scrubs every console message, network row, page error and
websocket URL on the event loop. Each call rebuilt the ``word_bounded`` set and
ran every compiled variant pattern (values x ~20 encodings) over every string
field, so after one typed password a 500-request page cost ~10^5 regex passes
per load. The ledger now keeps, per state, the set and one combined pattern
that decides whether a string holds any value at all; only a string that does
is scrubbed pattern by pattern, exactly as before.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros import privacy, scrub_engine
from octowright.macros.privacy import PrivacyLedger, scrub_sensitive_values

SECRET = "Hunter2*Pass_word"  # pragma: allowlist secret (synthetic fixture)
SHORT = "ab1"  # under the word-bounded length
TYPED = "admin"  # admitted word-bounded, like a typed password

CORPUS: list[Any] = [
    "",
    "nothing to see here",
    f"pw={SECRET}&next=1",
    f"https://x.test/?p={SECRET.replace('*', '%2A').replace('_', '%5F')}",
    f"https://x.test/?p={SECRET.replace('*', '%2a')}",
    f'{{"password": "{SECRET}"}}',
    "Hunter2\\*Pass\\_word in markdown",
    "Hunter2*Pass_word&amp;Hunter2*Pass_word",
    f"{SHORT} and xab1y and ab1-",
    'administrator #admin-menu admin_panel pw=admin "admin"',
    "[REDACTED] literal marker",
    {"url": f"/q?s={SECRET}", SECRET: [f"a {TYPED} b", ("t", SHORT), 3, None]},
    [f"{SECRET}{SECRET}", {"nested": {"deep": f"x={TYPED}"}}],
]


def _ledger() -> PrivacyLedger:
    ledger = PrivacyLedger([SECRET, SHORT])
    ledger.add([TYPED], word_bounded=True)
    return ledger


def _reference(ledger: PrivacyLedger, value: Any) -> Any:
    """The uncached scrub, straight from the module-level function."""
    return scrub_sensitive_values(value, ledger.values, word_bounded=ledger.word_bounded)


@pytest.mark.parametrize("value", CORPUS)
def test_the_cached_scrub_is_byte_identical_to_the_uncached_one(value: Any) -> None:
    ledger = _ledger()
    expected = _reference(ledger, value)
    first = ledger.scrub(value)  # builds the cache
    second = ledger.scrub(value)  # reads it
    assert repr(first) == repr(expected)
    assert repr(second) == repr(expected)


def test_a_string_that_holds_no_value_is_not_scrubbed_pattern_by_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _ledger()
    calls: list[str] = []
    real = scrub_engine._scrub_text
    monkeypatch.setattr(scrub_engine, "_scrub_text", lambda text, *a, **kw: calls.append(text) or real(text, *a, **kw))
    row = {"url": "https://cdn.test/app.js", "method": "GET", "status": 200, "type": "script"}
    assert ledger.scrub(row) == row
    assert ledger.scrub(f"pw={SECRET}") == f"pw={privacy.REDACTED}"
    assert calls == [f"pw={SECRET}"]


def test_the_word_bounded_set_is_not_rebuilt_per_call() -> None:
    ledger = _ledger()
    assert ledger.word_bounded is ledger.word_bounded
    assert ledger.word_bounded == frozenset({TYPED})


def test_adding_a_value_invalidates_the_cache() -> None:
    ledger = _ledger()
    later = "Later-Admitted-9"  # pragma: allowlist secret (synthetic fixture)
    assert ledger.scrub(f"x={later}") == f"x={later}"
    ledger.add([later])
    assert ledger.scrub(f"x={later}") == f"x={privacy.REDACTED}"
    for value in CORPUS:
        assert repr(ledger.scrub(value)) == repr(_reference(ledger, value))


def test_admitting_a_word_bounded_value_anywhere_invalidates_the_cache() -> None:
    """No new member, only a stronger claim on one -- still a new state."""
    ledger = _ledger()
    assert ledger.scrub("administrator") == "administrator"
    ledger.add([TYPED])
    assert ledger.word_bounded == frozenset()
    assert ledger.scrub("administrator") == f"{privacy.REDACTED}istrator"


def test_an_empty_ledger_returns_the_value_itself() -> None:
    row = {"a": "b"}
    assert PrivacyLedger().scrub(row) is row
