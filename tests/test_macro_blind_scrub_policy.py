# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Policy contract for values admitted to macro blind scrubbers."""

from __future__ import annotations

import pytest

from octowright.macros.privacy import (
    BLIND_SCRUB_POLICY_ENV,
    MacroBlindScrubRejected,
    blind_scrub_arg_values,
    blind_scrub_policy,
    classified_arg_values,
    redact_args,
    scrub_sensitive_values,
    sensitive_arg_values,
)


@pytest.fixture(autouse=True)
def _default_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BLIND_SCRUB_POLICY_ENV, raising=False)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "credentials"),
        ("credentials", "credentials"),
        (" ALL ", "all"),
        ("ReJeCt", "reject"),
    ],
)
def test_policy_defaults_and_normalizes(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: str) -> None:
    if raw is not None:
        monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, raw)

    assert blind_scrub_policy() == expected


def test_unknown_policy_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, "sometimes")

    with pytest.raises(ValueError, match=BLIND_SCRUB_POLICY_ENV):
        blind_scrub_policy()


def test_default_admits_only_credentials() -> None:
    args = {
        "session": "1",
        "user": "admin",
        "email": "me@example.test",
        "password": "p",  # pragma: allowlist secret
    }

    assert blind_scrub_arg_values(args) == ("p",)
    assert set(sensitive_arg_values(args)) == {"1", "admin", "me@example.test", "p"}


def test_default_preserves_the_issue_247_recording_row() -> None:
    row = {
        "action": "click",
        "selector": "li:nth-child(1) > a",
        "url": "https://shop.test/list?page=1&sort=11",
        "text": "Administrator panel, admin tools, 1 item",
    }

    for args in ({"session": "1"}, {"user": "admin"}):
        assert scrub_sensitive_values(row, blind_scrub_arg_values(args)) == row


def test_all_preserves_legacy_blind_scrubbing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, "all")

    values = blind_scrub_arg_values({"session": "1", "password": "p"})  # pragma: allowlist secret

    assert values == ("1", "p")
    assert scrub_sensitive_values("page=1 password=p", values) == "page=<redacted> password=<redacted>"


def test_reject_names_paths_and_tiers_without_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, "reject")
    canary = "A4-REJECT-PRIVATE-CANARY"

    with pytest.raises(MacroBlindScrubRejected) as caught:
        blind_scrub_arg_values({"payload": {"user": canary, "email": "person@example.test"}})

    rendered = str(caught.value)
    assert "payload.user (contextual)" in rendered
    assert "payload.email (identity)" in rendered
    assert canary not in rendered
    assert "person@example.test" not in rendered


def test_reject_accepts_credential_only_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, "reject")

    assert blind_scrub_arg_values({"password": "1"}) == ("1",)  # pragma: allowlist secret


def test_nested_credential_raises_an_inherited_contextual_tier() -> None:
    args = {"user": {"password": "short", "display": "ordinary"}}  # pragma: allowlist secret

    classified = classified_arg_values(args)

    assert [(item.path, item.tier) for item in classified] == [
        ("user.display", "contextual"),
        ("user.password", "credential"),
    ]
    assert blind_scrub_arg_values(args) == ("short",)


def test_non_field_mapping_keys_inherit_their_branch_tier() -> None:
    keyed_identity = "person@example.test"
    args = {"credential": {keyed_identity: "secret-value"}}  # pragma: allowlist secret

    classified = classified_arg_values(args)

    assert {(item.value, item.tier) for item in classified} == {
        (keyed_identity, "credential"),
        ("secret-value", "credential"),
    }


def test_structural_redaction_does_not_blindly_rewrite_aliases() -> None:
    args = {"user": "admin", "note": "Administrator panel, admin tools"}

    assert redact_args(args) == {
        "user": "<redacted>",
        "note": "Administrator panel, admin tools",
    }
