# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Versioned privacy rules shared by macro execution and generated exports."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from itertools import pairwise
from typing import Any
from urllib.parse import quote, quote_plus

ARG_PRIVACY_CLASSIFIER_VERSION = 3
REDACTED = "<redacted>"

# Three classifiers decided sensitivity independently -- this one,
# ``artifacts.redaction.is_sensitive_key`` and
# ``macros.substitution.is_credential_arg`` -- and disagreed on 1173 of 1916
# sampled names. The disagreements were holes, not opinions: ``private_key``,
# ``cookie`` and ``set_cookie`` reached args_used and generated exports in
# cleartext because only the redaction classifier knew them, ``otp`` was known
# only to the sink guard, and plural forms bypassed all three.
#
# The vocabulary below is the UNION of what all three matched at 0.23.0, frozen
# in tests/fixtures/privacy_classifier_baseline.json and enforced by
# tests/test_macro_privacy_vocabulary.py. Narrowing any entry re-opens a hole.
#
# Match mode is per token because the classifiers did not agree on that either:
# redaction matched by substring (so ``secret`` caught ``supersecretkey``) while
# this one matched by token. Substring is used only where the token is long and
# unambiguous; ``user`` must stay token-matched because ``browser`` contains it,
# and ``auth`` because ``author`` and ``authority`` do.
CREDENTIAL_SUBSTRING_TOKENS = frozenset(
    {
        "access_key",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "passphrase",
        "passwd",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
CREDENTIAL_TOKEN_TOKENS = frozenset({"auth", "authentication", "bearer", "cookie", "cookies", "otp", "pw", "pwd"})
IDENTITY_TOKEN_TOKENS = frozenset({"email", "phone", "username"})
CONTEXTUAL_TOKEN_TOKENS = frozenset({"contact", "peer", "session", "subject", "user"})

SUBSTRING_TOKENS = CREDENTIAL_SUBSTRING_TOKENS
TOKEN_TOKENS = CREDENTIAL_TOKEN_TOKENS | IDENTITY_TOKEN_TOKENS | CONTEXTUAL_TOKEN_TOKENS
#: Retained as the flat union so existing importers (notably the generated-script
#: template) keep resolving; the match mode lives in the two sets above.
SENSITIVE_KEY_TOKENS = SUBSTRING_TOKENS | TOKEN_TOKENS
SENSITIVE_KEY_PAIRS = frozenset({("api", "key"), ("access", "key")})

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
#: Below this length a trailing ``s`` is far more likely to be part of the word
#: than a plural marker, and the vocabulary's own short tokens (``pw``, ``pwd``)
#: must never be rewritten.
DEPLURALIZE_MIN_LENGTH = 5


def key_tokens(key: object) -> tuple[str, ...]:
    text = _CAMEL_BOUNDARY.sub(r"\1_\2", str(key))
    return tuple(part for part in _NON_ALNUM.split(text.lower()) if part)


def _normalized_key(key: object) -> str:
    return "_".join(key_tokens(key))


def _depluralized(token: str) -> str:
    if len(token) >= DEPLURALIZE_MIN_LENGTH and token.endswith("s"):
        return token[:-1]
    return token


def _token_candidates(tokens: tuple[str, ...]) -> set[str]:
    """Both spellings, never a replacement.

    Depluralizing destructively would mangle tokens that merely end in ``s``
    without being plural -- ``access``, ``address``, ``pass``, ``process`` --
    and for ``access`` that would break the ``access``/``key`` pair outright.
    """
    return set(tokens) | {_depluralized(token) for token in tokens}


def _matches(key: object, substrings: frozenset[str], whole_tokens: frozenset[str]) -> bool:
    normalized = _normalized_key(key)
    if any(token in normalized for token in substrings):
        return True
    tokens = key_tokens(key)
    if _token_candidates(tokens) & whole_tokens:
        return True
    return bool(set(pairwise(tokens)).intersection(SENSITIVE_KEY_PAIRS))


def is_credential_key(key: object) -> bool:
    """The tier that gates the credential sink guard, not merely redaction.

    Identity is deliberately excluded: ``{{order_id}}`` and an email in a URL are
    the ordinary parameterized-navigation case, while a password there is
    exfiltration.
    """
    return _matches(key, CREDENTIAL_SUBSTRING_TOKENS, CREDENTIAL_TOKEN_TOKENS)


def is_sensitive_arg_key(key: object) -> bool:
    return _matches(key, SUBSTRING_TOKENS, TOKEN_TOKENS)


_MAX_ENCODING_DEPTH = 3

# A mapping key that reads as structure rather than as data. Nested keys under a
# classified branch are collected because a map can be *keyed* by an identity
# (``{"credential": {"user@host": ...}}``), but the collected set feeds a blind
# substring scrub over every returned diagnostic, so a plain field name
# ("name", "role", "id") collected there rewrites unrelated failure text --
# ``[name=q]`` became ``[<redacted>=q]`` -- destroying the macro failure bundle
# this scrub exists to keep safe. Honest limit: an identifier-shaped secret used
# as a mapping key is not collected from the key position; it is still collected
# wherever it appears as a value.
FIELD_NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_-]*"
_FIELD_NAME_RE = re.compile(FIELD_NAME_PATTERN)


def is_field_name(key: object) -> bool:
    return bool(_FIELD_NAME_RE.fullmatch(str(key)))


def _is_identity_key(key: object) -> bool:
    """A key under a classified branch that reads as data rather than structure."""
    return key not in (None, "") and not is_field_name(key)


def _collect_from_mapping(value: Mapping[Any, Any], *, inherited: bool) -> set[str]:
    values: set[str] = set()
    for key, item in value.items():
        if inherited and _is_identity_key(key):
            values.add(str(key))
        values.update(_collect_sensitive_values(item, inherited=inherited or is_sensitive_arg_key(key)))
    return values


def _collect_sensitive_values(value: Any, *, inherited: bool) -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        values.update(_collect_from_mapping(value, inherited=inherited))
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


# Below this length a value is short enough to occur inside unrelated words, so
# it only matches on an alphanumeric boundary. Longer values match anywhere:
# a credential split across a word boundary must still be caught.
_WORD_BOUNDED_BELOW = 4


def _scrub_text(text: str, sensitive_values: tuple[str, ...], marker: str) -> str:
    for sensitive in sensitive_values:
        for variant in _serialized_variants(sensitive):
            pattern = (
                rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])"
                if len(sensitive) < _WORD_BOUNDED_BELOW
                else re.escape(variant)
            )
            # Percent-encoded spellings vary in hex case between producers.
            flags = re.IGNORECASE if "%" in variant else 0
            text = re.sub(pattern, marker, text, flags=flags)
    return text


def scrub_sensitive_values(value: Any, sensitive_values: tuple[str, ...], *, marker: str = REDACTED) -> Any:
    """Copy a diagnostic while scrubbing raw, escaped, and URL-encoded values."""
    if isinstance(value, str):
        return _scrub_text(value, sensitive_values, marker)
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
