# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.

"""Versioned privacy rules shared by macro execution and generated exports."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from itertools import pairwise
from typing import Any
from urllib.parse import quote, quote_plus

ARG_PRIVACY_CLASSIFIER_VERSION = 2
REDACTED = "<redacted>"

SENSITIVE_KEY_TOKENS = frozenset(
    {
        "apikey",
        "auth",
        "authentication",
        "authorization",
        "bearer",
        "contact",
        "credential",
        "email",
        "passphrase",
        "passwd",
        "password",
        "peer",
        "phone",
        "pwd",
        "pw",
        "secret",
        "session",
        "subject",
        "token",
        "user",
        "username",
    }
)
SENSITIVE_KEY_PAIRS = frozenset({("api", "key"), ("access", "key")})


def key_tokens(key: object) -> tuple[str, ...]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return tuple(part for part in re.split(r"[^A-Za-z0-9]+", text.lower()) if part)


def is_sensitive_arg_key(key: object) -> bool:
    tokens = key_tokens(key)
    return bool(
        any(token in SENSITIVE_KEY_TOKENS for token in tokens)
        or set(pairwise(tokens)).intersection(SENSITIVE_KEY_PAIRS)
    )


_MAX_ENCODING_DEPTH = 3


def _collect_sensitive_values(value: Any, *, inherited: bool) -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            branch_sensitive = inherited or is_sensitive_arg_key(key)
            if inherited and key not in (None, ""):
                values.add(str(key))
            values.update(_collect_sensitive_values(item, inherited=branch_sensitive))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            values.update(_collect_sensitive_values(item, inherited=inherited))
    elif inherited and value not in (None, ""):
        values.add(str(value))
    return values


def sensitive_arg_values(args: Mapping[str, Any]) -> tuple[str, ...]:
    values: set[str] = set()
    for key, value in args.items():
        values.update(_collect_sensitive_values(value, inherited=is_sensitive_arg_key(key)))
    return tuple(sorted(values, key=len, reverse=True))


def _serialized_variants(value: str) -> tuple[str, ...]:
    variants: set[str] = {
        value,
        json.dumps(value, ensure_ascii=True)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
    }
    frontier = set(variants)
    for _ in range(_MAX_ENCODING_DEPTH):
        frontier = {encoded for item in frontier for encoded in (quote(item, safe=""), quote_plus(item, safe=""))}
        variants.update(frontier)
    return tuple(sorted((item for item in variants if item), key=len, reverse=True))


def sensitive_value_variants(values: tuple[str, ...]) -> tuple[str, ...]:
    """Return bounded raw/serialized spellings for an in-memory visual boundary."""
    variants = {variant for value in values for variant in _serialized_variants(value)}
    return tuple(sorted(variants, key=len, reverse=True))


def scrub_sensitive_values(value: Any, sensitive_values: tuple[str, ...], *, marker: str = REDACTED) -> Any:
    """Copy a diagnostic while scrubbing raw, escaped, and URL-encoded values."""
    if isinstance(value, str):
        for sensitive in sensitive_values:
            variants = _serialized_variants(sensitive)
            for variant in variants:
                flags = re.IGNORECASE if "%" in variant else 0
                if len(sensitive) < 4:
                    value = re.sub(
                        rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])",
                        marker,
                        value,
                        flags=flags,
                    )
                else:
                    value = re.sub(re.escape(variant), marker, value, flags=flags)
        return value
    if isinstance(value, Mapping):
        return {
            str(scrub_sensitive_values(str(key), sensitive_values, marker=marker)): scrub_sensitive_values(
                item, sensitive_values, marker=marker
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_sensitive_values(item, sensitive_values, marker=marker) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_sensitive_values(item, sensitive_values, marker=marker) for item in value)
    return value


def _redact_nested_args(value: Any, marker: str) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): marker if is_sensitive_arg_key(key) else _redact_nested_args(item, marker)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_nested_args(item, marker) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_nested_args(item, marker) for item in value)
    return value


def redact_args(args: Mapping[str, Any], *, marker: str = REDACTED) -> dict[str, Any]:
    redacted = {
        str(key): marker if is_sensitive_arg_key(key) else _redact_nested_args(value, marker)
        for key, value in args.items()
    }
    return scrub_sensitive_values(redacted, sensitive_arg_values(args), marker=marker)


class SensitiveRecorder:
    """Scrub macro values at the recorder boundary before any durable write."""

    def __init__(self, recorder: Any, sensitive_values: tuple[str, ...]) -> None:
        self._recorder = recorder
        self._sensitive_values = sensitive_values

    def record(self, action: str, **fields: Any) -> None:
        self._recorder.record(action, **scrub_sensitive_values(fields, self._sensitive_values))

    def record_control(self, action: str, **fields: Any) -> None:
        self._recorder.record_control(action, **scrub_sensitive_values(fields, self._sensitive_values))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._recorder, name)


def install_sensitive_recorder(session: Any, sensitive_values: tuple[str, ...]) -> None:
    recorder = getattr(session, "recorder", None)
    if sensitive_values and recorder is not None:
        session.recorder = SensitiveRecorder(recorder, sensitive_values)
