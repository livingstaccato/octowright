# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Edge branches of ``lint_urls.url_carries_credential``.

Covers the unparsable-URL guard, a blank query value, and a token-shaped value
under an innocuous parameter NAME (the value-shape path, distinct from the
secret-param-name path the main suite covers).

The userinfo guard used to be a SECOND ``except ValueError`` nested inside the
first, and was dead by construction: CPython's ``_NetlocResultMixin._userinfo``
is a pure ``rpartition``/``partition`` with no raise in any version, so the only
way to reach it was to monkeypatch ``urlsplit`` with a fake -- covered without
being exercised. ``.username``/``.password`` re-parse the netloc lazily, so the
access now sits inside the SAME ``try`` as ``urlsplit`` itself and a malformed
netloc reaches it through a real URL string instead of a stub.
"""

from __future__ import annotations

from octowright.macros import lint_urls


def test_unparsable_url_defers_rather_than_denying() -> None:
    """A bracket in the netloc reads as malformed IPv6 -- urlsplit raises.

    None, not False: "I cannot parse this" is not "there is no credential
    here", and the caller falls back to its generic heuristics.
    """
    assert lint_urls.url_carries_credential("http://[not-valid-ipv6/x") is None


def test_lazily_raising_userinfo_is_reached_through_the_same_guard() -> None:
    """`.username` re-parses the netloc, so it raises on a real URL string --
    no stub needed, and the branch is genuinely exercised."""
    assert lint_urls.url_carries_credential("https://admin:hunter2@[bad/x") is None


def test_blank_query_value_is_skipped() -> None:
    assert lint_urls.url_carries_credential("https://example.com/x?ref=") is False


def test_token_shaped_value_under_an_innocuous_param_name_is_flagged() -> None:
    """The param NAME ('cb') carries no secret signal -- only the VALUE does."""
    value = "AKIAABCDEFGHIJKLMNOP"  # pragma: allowlist secret -- AWS-access-key-shaped
    assert lint_urls.url_carries_credential(f"https://example.com/x?cb={value}") is True


def test_ordinary_value_under_an_innocuous_param_name_is_not_flagged() -> None:
    assert lint_urls.url_carries_credential("https://example.com/x?cb=42") is False
