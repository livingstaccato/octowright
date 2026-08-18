# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Which PART of a URL (or JS expression) a credential can hide in.

0.14.4 narrowed URL credential detection from "run the password blob heuristic
on the whole string" to "scan userinfo and query". That killed the noise but
traded away real detection: the OAuth 2.0 implicit grant returns its access
token in the FRAGMENT by specification, and magic-link/reset tokens live in the
PATH. It also left the value-side signal as a Shannon-entropy test that both
over-fires on ordinary long query values and — because max entropy over a
16-symbol alphabet is exactly log2(16) = 4.0 against a strict `> 4.0` — cannot
fire on a hex digest at all.

These tests pin both directions: the parts that can carry a secret are scanned
with a signal precise enough not to cry wolf on the parts that cannot.
"""

from __future__ import annotations

import pytest

from octowright.macros.lint import lint_macro
from octowright.macros.lint_urls import code_carries_credential, url_carries_credential

# Synthetic, structurally-valid-looking values. None is a live credential.
_OAUTH_FRAGMENT_TOKEN = "ya29.A0ARrdaM9xKqvBqA7Zk3xyz"  # pragma: allowlist secret
_PATH_PAT = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"  # pragma: allowlist secret
_MD5 = "d41d8cd98f00b204e9800998ecf8427e"  # pragma: allowlist secret -- md5 of the empty string


@pytest.mark.parametrize(
    ("url", "why"),
    [
        (f"https://app.example.com/cb#access_token={_OAUTH_FRAGMENT_TOKEN}", "OAuth implicit grant, by spec"),
        ("https://app.example.com/cb#id_token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "OIDC id_token in fragment"),
        (f"https://api.example.com/v1/reset/{_PATH_PAT}", "magic-link / reset token in the path"),
        (f"https://api.example.com/data?key={_MD5}", "hex API key under a secret-ish name"),
        ("https://h/x?accessToken=abc", "camelCase name that the old regex missed"),
        ("https://h/x?passphrase=abc", "spelling the old regex missed"),
        ("https://h/x?private_key=abc", "spelling the old regex missed"),
        ("https://admin:hunter2@host/x", "userinfo is a credential by construction"),
    ],
)
def test_credential_bearing_parts_are_detected(url: str, why: str) -> None:
    assert url_carries_credential(url) is True, why


@pytest.mark.parametrize(
    ("url", "why"),
    [
        (
            "https://example.com/x?redirect_uri=https%3A%2F%2Fclient.example.org%2Fcb",
            "the canonical public OAuth redirect_uri is not a secret",
        ),
        ("https://example.com/r?title=Quarterly%20Business%20Review%202026%20Q3", "a human title is not a secret"),
        ("https://example.com/?next=/orders/12345/confirmation", "a return path is not a secret"),
        ("https://example.com/v2/reports/8f3a91c2", "a short path id is a resource name"),
        ("https://example.com/?order_id=550e8400-e29b-41d4-a716-446655440000", "a UUID is an identifier"),
        ("https://example.com/?utm_campaign=summer-2026-launch-promo-email", "a campaign slug is not a secret"),
        ("https://h/x?auth_url=https://idp.test", "auth_url NAMES an endpoint, it does not carry a secret"),
        ("https://app.example.com/orders?page=1", "the plain case that started this whole fix"),
    ],
)
def test_ordinary_urls_do_not_cry_wolf(url: str, why: str) -> None:
    assert url_carries_credential(url) is False, why


def test_unparsable_url_defers_to_the_caller_instead_of_asserting_no_credential() -> None:
    """`urlsplit` raises on an unbracketed-colon netloc.

    Returning False there means a literal basic-auth credential in a slightly
    malformed URL is silently accepted by a credential linter. None means "I
    cannot parse this", so the caller applies its generic heuristics.
    """
    assert url_carries_credential("https://admin:hunter2@[bad/x") is None


def test_malformed_url_with_a_credential_is_still_flagged_end_to_end() -> None:
    issues = lint_macro({"name": "t", "actions": [{"action": "navigate", "url": "https://admin:hunter2@[bad/x"}]})
    assert "looks_like_credential" in [i.code for i in issues]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("document.querySelector('#total').value * 2", False),
        ("window.__APP__.v2.count > 0", False),
        (f"fetch('/x', {{headers: {{Authorization: 'Bearer {_PATH_PAT}'}}}})", True),
    ],
)
def test_js_expressions_are_scanned_for_embedded_tokens_not_blob_shape(expression: str, expected: bool) -> None:
    """`expression` used the same 12-chars-with-a-special heuristic as a URL,
    which describes ordinary JS at least as well as it describes a password."""
    assert code_carries_credential(expression) is expected


@pytest.mark.parametrize(
    "action",
    [
        {"action": "expect_url", "pattern": "https://app.example.com/orders?page=1"},
        {"action": "mock_route", "pattern": "https://api.example.com/v1/users?limit=25", "status": 200},
        {"action": "unmock_route", "pattern": "**/api/v1/**"},
        {"action": "evaluate", "expression": "document.querySelector('#total').value * 2"},
        {"action": "expect_js", "expression": "window.__APP__.v2.count > 0"},
    ],
)
def test_url_and_code_shaped_fields_stop_warning_on_ordinary_values(action: dict[str, object]) -> None:
    """The 0.14.4 fix special-cased only the literal key `url`, so the noise
    relocated to `pattern` and `expression` instead of going away."""
    issues = [i for i in lint_macro({"name": "t", "actions": [action]}) if i.code == "looks_like_credential"]
    assert issues == [], [i.message for i in issues]


def test_a_real_credential_in_a_pattern_is_still_flagged() -> None:
    action = {"action": "mock_route", "pattern": f"https://api.example.com/v1?api_key={_MD5}", "status": 200}
    issues = [i for i in lint_macro({"name": "t", "actions": [action]}) if i.code == "looks_like_credential"]
    assert len(issues) == 1
