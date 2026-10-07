# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Macro argument classification: plurals, nested paths, the policy, and the declared-public floor."""

from __future__ import annotations

import pytest

from octowright.macros.privacy import (
    BLIND_SCRUB_POLICY_ENV,
    ClassifiedArgValue,
    MacroArgPrivacy,
    MacroBlindScrubRejected,
    ScrubExemption,
    assertion_digest_matches,
    assertion_text_digest,
    blind_scrub_arg_values,
    is_sensitive_arg_key,
)
from octowright.macros.scrub_admission import SCRUB_COMMON_VALUES_ENV, SCRUB_MIN_LENGTH_ENV, SCRUB_RUN_SCOPED_ENV


@pytest.fixture(autouse=True)
def _default_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (BLIND_SCRUB_POLICY_ENV, SCRUB_MIN_LENGTH_ENV, SCRUB_COMMON_VALUES_ENV, SCRUB_RUN_SCOPED_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("key", ["users", "emails", "phones", "sessions", "subjects"])
def test_a_plural_of_a_classified_token_is_classified(key: str) -> None:
    assert is_sensitive_arg_key(key) is True


@pytest.mark.parametrize("key", ["pws", "otps", "pas", "bus"])
def test_a_short_token_ending_in_s_is_not_depluralized(key: str) -> None:
    assert is_sensitive_arg_key(key) is False


def test_scrub_exemption_rows_carry_the_macro_only_when_named() -> None:
    exemption = ScrubExemption("email", "identity", "common")

    assert exemption.as_dict() == {"path": "email", "tier": "identity", "reason": "common"}
    assert exemption.as_dict("login") == {"macro": "login", "path": "email", "tier": "identity", "reason": "common"}


def test_with_run_scoping_off_credentials_stay_persistent_beside_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_RUN_SCOPED_ENV, "off")
    admission = MacroArgPrivacy().admission(
        {"password": "zebrin4-secret", "email": "alice@example.com"},  # pragma: allowlist secret
        policy="all",
    )

    assert admission.persistent == ("alice@example.com", "zebrin4-secret")
    assert admission.run_scoped == ()


def test_classified_paths_name_sequences_sets_and_non_field_keys() -> None:
    args = {
        "password": ["-".join(("pw", "one")), {"pw-two", 3}],
        "credential": {"user@host": "token-x", "": "token-y"},
        "": {"secret": "nested-secret"},  # pragma: allowlist secret
    }

    assert MacroArgPrivacy().classified(args) == (
        ClassifiedArgValue("token-x", "credential[<key>]", "credential"),
        ClassifiedArgValue("token-y", "credential[<key>]", "credential"),
        ClassifiedArgValue("user@host", "credential[<key>]", "credential"),
        ClassifiedArgValue("-".join(("pw", "one")), "password[0]", "credential"),
        ClassifiedArgValue("pw-two", "password[1][0]", "credential"),
        ClassifiedArgValue("3", "password[1][1]", "credential"),
        ClassifiedArgValue("nested-secret", "secret", "credential"),
    )


def test_set_members_are_numbered_in_repr_order() -> None:
    # repr("b'") starts with a double quote, which sorts before every other spelling here.
    assert MacroArgPrivacy().classified({"password": {"a1", "b'"}}) == (
        ClassifiedArgValue("b'", "password[0]", "credential"),
        ClassifiedArgValue("a1", "password[1]", "credential"),
    )


def test_a_stronger_tier_sorts_first_at_the_same_path() -> None:
    args = {"email": {"my password": "".join(("s3", "cr"))}}  # pragma: allowlist secret

    assert MacroArgPrivacy().classified(args) == (
        ClassifiedArgValue("".join(("s3", "cr")), "email[<key>]", "credential"),
        ClassifiedArgValue("my password", "email[<key>]", "identity"),
    )


def test_reject_names_every_refused_argument() -> None:
    with pytest.raises(MacroBlindScrubRejected) as excinfo:
        MacroArgPrivacy().admission({"email": "alice@example.com", "user": "bob-smith"}, policy="reject")

    assert str(excinfo.value) == "non-credential classified arguments refused: email (identity), user (contextual)"


def test_an_explicit_policy_overrides_the_configured_one() -> None:
    args = {"email": "alice@example.com", "password": "zebrin4-secret"}  # pragma: allowlist secret

    assert blind_scrub_arg_values(args, policy="all") == ("alice@example.com", "zebrin4-secret")
    assert MacroArgPrivacy().blind_scrub(args, policy="all") == ("alice@example.com", "zebrin4-secret")
    assert blind_scrub_arg_values(args) == ("zebrin4-secret",)


def test_a_declared_public_credential_name_is_still_redacted() -> None:
    privacy = MacroArgPrivacy(public_args=frozenset({"password", "nickname"}))

    assert privacy.tier_of("password") == "credential"
    assert privacy.redact({"password": "zebrin4-secret", "nickname": "bobby"}) == {  # pragma: allowlist secret
        "password": "<redacted>",
        "nickname": "bobby",
    }


def test_a_positional_argument_is_redacted_structurally_whatever_its_value() -> None:
    privacy = MacroArgPrivacy(credential_args=frozenset({"note"}))

    assert privacy.redact({"note": {"a": 1}, "flag": True}) == {"note": "<redacted>", "flag": True}


def test_redact_under_reject_still_renders_with_the_credential_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, "reject")

    assert MacroArgPrivacy().redact({"email": "alice@example.com", "note": "x"}) == {
        "email": "<redacted>",
        "note": "x",
    }


def test_redact_under_all_scrubs_identity_values_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, "all")

    assert MacroArgPrivacy().redact({"email": "alice@example.com", "note": "mail alice@example.com"}) == {
        "email": "<redacted>",
        "note": "mail <redacted>",
    }


def test_an_empty_value_never_matches_a_digest() -> None:
    assert assertion_digest_matches("", assertion_text_digest("")) is False
    assert assertion_digest_matches("abc", assertion_text_digest("abc")) is True
