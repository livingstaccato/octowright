# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The lint's noise floor: what it must NOT say, and the one thing it must.

A credential linter that fires on ordinary macros gets tuned out, and an
error-severity issue is not merely noisy -- ``PUT /api/macros/{name}`` gates
the save on ``error_count == 0``, so a false error makes a macro UNSAVABLE
through the dashboard. Each case here is a shape a real macro carries.

The one true-positive direction pinned here is the path scan, which claimed in
its docstring to catch magic-link and password-reset tokens while matching only
vendor prefixes -- a shape no magic-link token has.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.lint import lint_macro
from octowright.macros.lint_urls import url_carries_credential


def _issues(action: dict[str, Any]) -> list[Any]:
    return list(lint_macro({"name": "m", "actions": [action]}))


def _codes(action: dict[str, Any]) -> list[str]:
    return [i.code for i in _issues(action)]


# --- #8: an ignored screenshot field must not block the save -----------------


def test_unknown_screenshot_field_is_a_warning_not_an_error() -> None:
    """`_dispatch_standard` forwards only `path` for screenshot, so a stray
    field is DROPPED, not a TypeError. Reporting the drop is right; reporting
    it at error severity rejects the macro at PUT /api/macros/{name} for a
    condition the message itself calls harmless."""
    issues = [i for i in _issues({"action": "screenshot", "path": "a.png", "full_page": True})]
    unknown = [i for i in issues if i.code == "unknown_field"]
    assert unknown, "the dropped field must still be reported"
    assert all(i.severity == "warning" for i in unknown)


def test_unknown_field_on_a_dispatching_action_is_still_an_error() -> None:
    """Only screenshot is special-cased; everywhere else replay really raises."""
    issues = [i for i in _issues({"action": "wait_for", "js": "x"}) if i.code == "unknown_field"]
    assert issues and all(i.severity == "error" for i in issues)


# --- #9: identity params are not credentials ---------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://app.example.com/signup?email=ada@example.com",
        "https://app.example.com/signup?e-mail=ada@example.com",
        "https://app.example.com/invite?userName=ada",
        "https://app.example.com/invite?username=ada",
    ],
)
def test_identity_query_params_are_not_credentials(url: str) -> None:
    """`is_sensitive_key` counts `email`/`username` because a TYPED field by
    that name is half of a credential pair. A prefilled signup/invite link is
    the single most common URL a macro navigates to, and warning on every one
    of them is exactly the cry-wolf this module exists to avoid."""
    assert url_carries_credential(url) is False


def test_a_real_secret_param_still_fires_alongside_an_identity_param() -> None:
    assert url_carries_credential("https://x.test/cb?email=ada@example.com&access_token=abc123") is True


# --- #10: a long run of digits is an id, not a digest ------------------------


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("https://api.test/v1/orders?id=" + "9" * 32, "32-digit order id"),
        ("https://api.test/v1/events?cursor=" + "1234567890" * 4, "concatenated numeric cursor"),
    ],
)
def test_all_digit_runs_are_not_hex_secrets(url: str, why: str) -> None:
    """`^[0-9a-fA-F]{32,}$` matches pure digits. A real 32-char hex digest is
    all-digits with probability (10/16)**32, so requiring one hex LETTER costs
    nothing and stops flagging numeric ids -- the same letter+digit mix the
    opaque-run branch already demands."""
    assert url_carries_credential(url) is False, why


def test_a_hex_digest_with_letters_is_still_flagged() -> None:
    assert url_carries_credential("https://api.test/d?x=d41d8cd98f00b204e9800998ecf8427e") is True


# --- #11: the path scan must catch what its docstring promises ---------------


def test_magic_link_token_after_a_credential_context_segment_is_flagged() -> None:
    """The documented reason the path is scanned at all. A reset token is an
    opaque segment with no vendor prefix, so the prefix-only scan could never
    fire on it -- the branch was dead for its stated purpose."""
    assert url_carries_credential("https://app.test/reset/8f3a91c2b4e5d6a7089bce12f34a5678") is True


def test_a_jwt_anywhere_in_the_path_is_flagged() -> None:
    """`eyJ` + two dot-separated base64url runs is unambiguous, context-free."""
    # pragma: allowlist nextline secret -- jwt.io sample payload, not a live token
    url = "https://app.test/v2/eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"
    assert url_carries_credential(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://api.test/v2/reports/8f3a91c2b4e5d6a7089bce12f34a5678",
        "https://api.test/v2/customers/018f3a91c2b4e5d6a7089bce12f34a56",
    ],
)
def test_an_opaque_id_outside_a_credential_context_is_not_flagged(url: str) -> None:
    """The shape test alone cannot tell a reset token from a resource id, which
    is why it only runs on the segment FOLLOWING a credential context word."""
    assert url_carries_credential(url) is False
