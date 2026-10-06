# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Artifact key classification: separator and casing spellings of a sensitive key."""

from __future__ import annotations

import pytest

from octowright.artifacts.redaction import REDACTED_VALUE, is_sensitive_key, redact_mapping


@pytest.mark.parametrize("key", ["_set_cookie_", "-Set-Cookie-", "__pw__", "pass_word", "p_w", "Pass-Word", "api-Key"])
def test_separator_spellings_of_a_sensitive_key_are_sensitive(key: str) -> None:
    assert is_sensitive_key(key) is True


@pytest.mark.parametrize("key", ["_page_", "word", "pass", "set", "_x_"])
def test_ordinary_keys_are_not_sensitive(key: str) -> None:
    assert is_sensitive_key(key) is False


def test_a_separated_credential_key_is_redacted_in_a_mapping() -> None:
    assert redact_mapping({"pass_word": "zebrin4", "_set_cookie_": "sid=1", "page": "home"}) == {
        "pass_word": REDACTED_VALUE,
        "_set_cookie_": REDACTED_VALUE,
        "page": "home",
    }
