# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``looks_like_credential`` must not fire on ordinary navigation URLs.

``_looks_like_password`` rule 2 is ">=12 chars with a digit, a letter and a
special char". In a URL, ``:`` ``/`` ``.`` ``?`` ``=`` are all "special", so any
public https URL containing a single digit satisfies it. The warning then fires
on every other navigate action, and an operator learns to ignore the whole
check -- which is exactly when it stops catching the real thing.

A URL still HAS credential-bearing parts, so the check is narrowed rather than
removed: basic-auth userinfo and secret-ish query parameters still warn.
"""

from __future__ import annotations

import pytest

from octowright.macros.lint import Issue, _check_credentials


def _codes(action: dict[str, object]) -> list[str]:
    issues: list[Issue] = []
    _check_credentials(action, 0, issues)
    return [i.code for i in issues]


@pytest.mark.parametrize(
    "url",
    [
        "https://app.school.edu/a/b?x=1",
        "https://example.com/students/42",
        "http://127.0.0.1:6286/new-tab",
        "https://example.com/v2/reports?year=2026&page=3",
        "https://example.com/",
    ],
)
def test_plain_navigation_urls_are_not_flagged(url: str) -> None:
    assert _codes({"action": "navigate", "url": url}) == []


def test_basic_auth_userinfo_in_url_is_still_flagged() -> None:
    """Real credentials embedded in the URL must still warn."""
    assert "looks_like_credential" in _codes({"action": "navigate", "url": "https://admin:hunter2pass!@example.com/x"})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/cb?access_token=ya29.A0ARrdaM9xKqvBqA7Zk3",
        "https://example.com/x?api_key=sk-abc123def456ghi789",
        "https://example.com/x?password=s3cr3t-P%40ssw0rd",
    ],
)
def test_secret_bearing_query_parameters_are_still_flagged(url: str) -> None:
    assert "looks_like_credential" in _codes({"action": "navigate", "url": url})


def test_non_url_fields_keep_the_original_heuristic() -> None:
    """Narrowing applies to `url` only — a typed value is unchanged."""
    assert "looks_like_credential" in _codes({"action": "fill", "value": "hunter2-P@ssw0rd"})
    assert "looks_like_credential" in _codes({"action": "fill", "value": "user@example.com"})


def test_username_only_userinfo_is_flagged() -> None:
    """A bare username (no colon/password) is still basic-auth credential shape."""
    assert "looks_like_credential" in _codes({"action": "navigate", "url": "https://api-key-abc@example.com/x"})


def test_password_only_userinfo_is_flagged() -> None:
    """A bare password (empty username) is still a credential."""
    assert "looks_like_credential" in _codes({"action": "navigate", "url": "https://:hunter2pass@example.com/x"})


def test_issue_carries_the_action_index_not_a_stale_default() -> None:
    """The Issue for a later action in the macro must point AT that action."""
    issues: list[Issue] = []
    _check_credentials({"action": "navigate", "url": "https://a:b@example.com/x"}, 7, issues)
    assert [i.action_index for i in issues] == [7]


def test_issue_message_names_the_field_and_the_remediation_verbatim() -> None:
    issues: list[Issue] = []
    _check_credentials({"action": "navigate", "url": "https://a:b@example.com/x"}, 0, issues)
    assert issues[0].message == (
        "field 'url' looks like a literal credential (value redacted) — consider {{email}} parameterization"
    )


def test_a_non_string_candidate_field_does_not_abort_the_rest_of_the_scan() -> None:
    """One bad-typed field earlier in the dict must not hide a real credential
    in a LATER field — proves the loop uses `continue`, not a full `break`."""
    action = {"action": "fill", "expression": 123, "value": "hunter2-P@ssw0rd"}
    assert _codes(action) == ["looks_like_credential"]


def test_a_placeholder_candidate_field_does_not_abort_the_rest_of_the_scan() -> None:
    action = {"action": "fill", "text": "{{email}}", "value": "hunter2-P@ssw0rd"}
    assert _codes(action) == ["looks_like_credential"]
