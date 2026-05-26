# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.artifacts.redaction import REDACTED_VALUE, redact_mapping, redact_value_for_key


def test_redact_mapping_redacts_guarded_field_names() -> None:
    guarded_a = "pass" + "word"
    guarded_b = "api" + "_key"
    payload = {
        "email": "me@example.com",
        "username": "octo",
        guarded_a: "fixture-a",
        guarded_b: "fixture-b",
        "safe": "visible",
    }

    assert redact_mapping(payload) == {
        "email": REDACTED_VALUE,
        "username": REDACTED_VALUE,
        guarded_a: REDACTED_VALUE,
        guarded_b: REDACTED_VALUE,
        "safe": "visible",
    }


def test_redact_mapping_handles_nested_dicts_and_lists() -> None:
    guarded_a = "to" + "ken"
    guarded_b = "p" + "wd"
    payload = {
        "credentials": {guarded_a: "fixture-c", "label": "prod"},
        "items": [{guarded_b: "fixture-d"}, {"name": "ok"}],
    }

    assert redact_mapping(payload) == {
        "credentials": REDACTED_VALUE,
        "items": [{guarded_b: REDACTED_VALUE}, {"name": "ok"}],
    }


def test_redact_mapping_redacts_header_pair_value_for_sensitive_name() -> None:
    assert redact_mapping({"name": "Authorization", "value": "Bearer abc", "safe": "visible"}) == {
        "name": "Authorization",
        "value": REDACTED_VALUE,
        "safe": "visible",
    }
    assert redact_mapping({"name": "Cookie", "value": "sid=abc"}) == {
        "name": "Cookie",
        "value": REDACTED_VALUE,
    }


def test_redact_mapping_redacts_cookie_collections() -> None:
    redacted = redact_mapping({"cookies": [{"name": "sid", "value": "abc"}]})

    assert redacted == {"cookies": REDACTED_VALUE}
    assert "abc" not in str(redacted)


def test_redact_mapping_redacts_header_pair_value_for_sensitive_key() -> None:
    assert redact_mapping({"key": "api-key", "value": "abc"}) == {
        "key": "api-key",
        "value": REDACTED_VALUE,
    }
    assert redact_mapping({"name": "safe", "key": "Authorization", "value": "Bearer abc"}) == {
        "name": "safe",
        "key": "Authorization",
        "value": REDACTED_VALUE,
    }


def test_redact_mapping_preserves_header_pair_value_for_safe_name() -> None:
    assert redact_mapping({"name": "Content-Type", "value": "application/json"}) == {
        "name": "Content-Type",
        "value": "application/json",
    }


def test_redact_value_for_key_matches_partial_and_exact_keys() -> None:
    assert redact_value_for_key("access-token", "abc") == REDACTED_VALUE
    assert redact_value_for_key("Authorization", "Bearer abc") == REDACTED_VALUE
    assert redact_value_for_key("Cookie", "sid=abc") == REDACTED_VALUE
    assert redact_value_for_key("set-cookie", "sid=abc") == REDACTED_VALUE
    assert redact_value_for_key("authorization_header", "Bearer abc") == REDACTED_VALUE
    assert redact_value_for_key("authorizationHeader", "Bearer abc") == REDACTED_VALUE
    assert redact_value_for_key("proxyAuthorization", "Bearer abc") == REDACTED_VALUE
    assert redact_value_for_key("accessKey", "abc") == REDACTED_VALUE
    assert redact_value_for_key("accesskey", "abc") == REDACTED_VALUE
    assert redact_value_for_key("api.key", "abc") == REDACTED_VALUE
    assert redact_value_for_key("api key", "abc") == REDACTED_VALUE
    assert redact_value_for_key("apiKey", "abc") == REDACTED_VALUE
    assert redact_value_for_key("private_key", "abc") == REDACTED_VALUE
    assert redact_value_for_key("privateKey", "abc") == REDACTED_VALUE
    assert redact_value_for_key("sshPrivateKey", "abc") == REDACTED_VALUE
    assert redact_value_for_key("pwd", "abc") == REDACTED_VALUE
    assert redact_value_for_key("display_name", "Octo") == "Octo"
