# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Edge branches of ``lint_urls.url_carries_credential``.

Covers the unparsable-URL guard, a blank query value, and a token-shaped
value under an innocuous parameter name (the ``token_like`` fallback path,
distinct from the secret-PARAM-NAME path the main lint suite already covers).

The username/password ValueError guard is defensive against a URL string
CPython's own urlsplit cannot actually produce -- verified empirically: any
netloc CPython 3.11 would raise ValueError on for username/password access,
it has already rejected earlier, at urlsplit() itself (a bracket in the
netloc reads as a malformed IPv6 host, caught by the outer except first). The
guard mirrors the outer one for defense against a future Python where that
stops being true, so it's exercised here with an injected fake rather than a
real URL, matching the outer split() failure's `# pragma: no cover`-adjacent
reasoning in lint_fields.py.
"""

from __future__ import annotations

import pytest

from octowright.macros import lint_urls


def _always_false(_value: str) -> bool:
    return False


def _always_true(_value: str) -> bool:
    return True


def test_unparsable_url_is_not_flagged() -> None:
    """A bracket in the netloc reads as malformed IPv6 -- urlsplit itself raises."""
    assert lint_urls.url_carries_credential("http://[not-valid-ipv6/x", token_like=_always_true) is False


def test_username_password_access_error_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercises the inner guard via an injected split result, since no real URL
    string reaches it in this Python version (see module docstring)."""

    class _RaisingUserinfo:
        query = ""

        @property
        def username(self) -> str:
            raise ValueError("simulated malformed userinfo")

        @property
        def password(self) -> str:
            raise ValueError("simulated malformed userinfo")

    monkeypatch.setattr(lint_urls, "urlsplit", lambda _raw: _RaisingUserinfo())
    assert lint_urls.url_carries_credential("http://irrelevant/", token_like=_always_true) is False


def test_blank_query_value_is_skipped() -> None:
    assert lint_urls.url_carries_credential("https://example.com/x?ref=", token_like=_always_true) is False


def test_token_shaped_value_under_an_innocuous_param_name_is_flagged() -> None:
    """The param NAME ('cb') carries no secret signal -- only the VALUE does."""
    value = "AKIAABCDEFGHIJKLMNOP"  # pragma: allowlist secret -- AWS-key-shaped, matches the injected token_like
    assert (
        lint_urls.url_carries_credential(f"https://example.com/x?cb={value}", token_like=lambda v: v == value) is True
    )


def test_ordinary_value_under_an_innocuous_param_name_is_not_flagged() -> None:
    assert lint_urls.url_carries_credential("https://example.com/x?cb=42", token_like=_always_false) is False
