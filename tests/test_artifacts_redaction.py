# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.artifacts.redaction import REDACTED_VALUE, redact_mapping, redact_value_for_key


def test_redact_mapping_redacts_sensitive_keys() -> None:
    payload = {
        "email": "me@example.com",
        "username": "octo",
        "password": "hunter2",  # pragma: allowlist secret
        "api_key": "abc123",  # pragma: allowlist secret
        "safe": "visible",
    }

    assert redact_mapping(payload) == {
        "email": REDACTED_VALUE,
        "username": REDACTED_VALUE,
        "password": REDACTED_VALUE,
        "api_key": REDACTED_VALUE,
        "safe": "visible",
    }


def test_redact_mapping_handles_nested_dicts_and_lists() -> None:
    payload = {
        "credentials": {"token": "secret", "label": "prod"},  # pragma: allowlist secret
        "items": [{"pwd": "x"}, {"name": "ok"}],  # pragma: allowlist secret
    }

    assert redact_mapping(payload) == {
        "credentials": REDACTED_VALUE,
        "items": [{"pwd": REDACTED_VALUE}, {"name": "ok"}],
    }


def test_redact_mapping_redacts_header_pair_value_for_sensitive_name() -> None:
    assert redact_mapping({"name": "Authorization", "value": "Bearer abc", "safe": "visible"}) == {
        "name": "Authorization",
        "value": REDACTED_VALUE,
        "safe": "visible",
    }


def test_redact_mapping_redacts_header_pair_value_for_sensitive_key() -> None:
    assert redact_mapping({"key": "api-key", "value": "abc"}) == {
        "key": "api-key",
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
