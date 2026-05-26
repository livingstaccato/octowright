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


def test_redact_value_for_key_matches_partial_and_exact_keys() -> None:
    assert redact_value_for_key("access-token", "abc") == REDACTED_VALUE
    assert redact_value_for_key("pwd", "abc") == REDACTED_VALUE
    assert redact_value_for_key("display_name", "Octo") == "Octo"
