# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REDACTED_VALUE = "<redacted>"

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "credential",
)
_SENSITIVE_EXACT_KEYS = frozenset({"pw", "pwd", "auth", "email", "username"})


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _SENSITIVE_EXACT_KEYS or any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_value_for_key(key: str, value: Any) -> Any:
    if is_sensitive_key(str(key)):
        return REDACTED_VALUE
    return redact_value(value)


def redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_mapping(values: Mapping[str, Any] | None) -> dict[str, Any]:
    if not values:
        return {}
    return {str(key): redact_value_for_key(str(key), value) for key, value in values.items()}
