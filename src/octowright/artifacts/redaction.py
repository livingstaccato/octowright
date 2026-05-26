# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED_VALUE = "<redacted>"

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "credential",
    "private_key",
)
_SENSITIVE_EXACT_KEYS = frozenset({"pw", "pwd", "auth", "email", "username", "cookie", "cookies", "set_cookie"})
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def is_sensitive_key(key: str) -> bool:
    normalized = _NON_ALNUM.sub("_", _CAMEL_CASE_BOUNDARY.sub("_", key).lower()).strip("_")
    compact = normalized.replace("_", "")
    return (
        normalized in _SENSITIVE_EXACT_KEYS
        or compact in _SENSITIVE_EXACT_KEYS
        or any(part in normalized or part.replace("_", "") in compact for part in _SENSITIVE_KEY_PARTS)
    )


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
    descriptors = (values.get("name"), values.get("key"))
    redact_pair_value = any(isinstance(descriptor, str) and is_sensitive_key(descriptor) for descriptor in descriptors)
    return {
        str(key): (
            REDACTED_VALUE
            if redact_pair_value and str(key) in {"value", "values"}
            else redact_value_for_key(str(key), value)
        )
        for key, value in values.items()
    }
