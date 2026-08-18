# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Two credential signals that were removed along with the entropy heuristic.

Replacing Shannon entropy with a structural test fixed real false positives,
but the replacement — one unbroken run of ``[A-Za-z0-9]{24,}`` — cannot match a
token that contains punctuation. A JWT is the motivating example the module
docstring itself cites (the OAuth implicit grant returns one in the fragment),
and base64url syntax puts ``.``, ``-`` and ``_`` inside it. The OAuth/OIDC cases
in the suite only pass because the parameter NAME is sensitive, so a live bearer
under an opaque name (``?t=``, ``?s=``, ``?c=``) linted clean.

Separately, routing ``expression`` to ``code_carries_credential`` — vendor
prefixes only — removed every non-vendor signal from ``evaluate``/``expect_js``.
``OCTOWRIGHT_REDACT_INPUTS`` only scrubs ``evaluate`` under ``all``, so in the
default ``passwords`` mode a cleartext password assigned in an expression is on
disk AND unflagged at save time.

Both are fixed with high-precision shapes rather than by restoring entropy:
a JWT is recognised by its structure, and a quoted string literal inside an
expression is scanned as the value it is.
"""

from __future__ import annotations

import pytest

from octowright.macros.lint import lint_macro
from octowright.macros.lint_urls import code_carries_credential, url_carries_credential
from tests.macro_lint._secret_shapes import MIXED_CLASS_VALUE, OPAQUE_ALNUM_VALUE, fake_jwt

_JWT = fake_jwt()


@pytest.mark.parametrize(
    "url",
    [
        f"https://app.example.com/cb?t={_JWT}",
        f"https://app.example.com/cb#{_JWT}",
        f"https://app.example.com/cb#s={_JWT}",
    ],
)
def test_a_jwt_is_a_credential_wherever_it_sits(url: str) -> None:
    assert url_carries_credential(url) is True


def test_jwt_detection_does_not_need_a_sensitive_parameter_name() -> None:
    """The pre-existing OAuth cases pass on the NAME; this one has no such name."""
    assert url_carries_credential(f"https://h/x?c={_JWT}") is True


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example.com/products?category=summer-2026-launch-promo",
        "https://docs.example.com/guide/getting-started/installation",
        "https://app.example.com/r?redirect_uri=https%3A%2F%2Fother.example.com%2Fdone",
        "https://api.example.com/v2/reports/8f3a91c2",
    ],
)
def test_ordinary_urls_stay_quiet(url: str) -> None:
    """The whole point of the structural rewrite — don't regress it."""
    assert url_carries_credential(url) is False


@pytest.mark.parametrize(
    "expression",
    [
        f"document.querySelector('#pw').value = '{MIXED_CLASS_VALUE}'",
        f'window.apiKey === "{OPAQUE_ALNUM_VALUE}"',
        f"localStorage.setItem('tok', '{_JWT}')",
    ],
)
def test_a_literal_secret_in_an_expression_is_flagged(expression: str) -> None:
    assert code_carries_credential(expression) is True
    assert [i.code for i in lint_macro({"actions": [{"action": "evaluate", "expression": expression}]})] == [
        "looks_like_credential"
    ]


@pytest.mark.parametrize(
    "expression",
    [
        "document.querySelector('#main .row > td:nth-child(2)').innerText",
        "window.scrollTo(0, document.body.scrollHeight)",
        "document.title === 'Order #1234 confirmed'",
        "[...document.querySelectorAll('a')].map(a => a.href)",
    ],
)
def test_ordinary_expressions_stay_quiet(expression: str) -> None:
    """A selector string is 12+ chars of mixed classes; it must not fire."""
    assert code_carries_credential(expression) is False
    assert lint_macro({"actions": [{"action": "evaluate", "expression": expression}]}) == []


def test_expect_js_is_scanned_the_same_way() -> None:
    action = {"action": "expect_js", "expression": f"window.token === '{OPAQUE_ALNUM_VALUE}'"}
    assert [i.code for i in lint_macro({"actions": [action]})] == ["looks_like_credential"]
