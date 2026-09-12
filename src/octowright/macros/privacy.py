# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Versioned privacy rules shared by macro execution and generated exports."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import Any
from urllib.parse import quote, quote_plus

ARG_PRIVACY_CLASSIFIER_VERSION = 4
REDACTED = "<redacted>"

# Three classifiers decided sensitivity independently -- this one,
# ``artifacts.redaction.is_sensitive_key`` and
# ``macros.substitution.is_credential_arg`` -- and disagreed on 1173 of 1916
# sampled names. The disagreements were holes, not opinions: ``private_key``,
# ``cookie`` and ``set_cookie`` reached args_used and generated exports in
# cleartext because only the redaction classifier knew them, ``otp`` was known
# only to the sink guard, and plural forms bypassed all three.
#
# The vocabulary below is the UNION of what all three matched before it (at c58a1461), frozen
# in tests/fixtures/privacy_classifier_baseline.json and enforced by
# tests/test_macro_privacy_vocabulary.py. Narrowing any entry re-opens a hole.
#
# Match mode is per token because the classifiers did not agree on that either:
# redaction matched by substring (so ``secret`` caught ``supersecretkey``) while
# this one matched by token. Substring is used only where the token is long and
# unambiguous; ``user`` must stay token-matched because ``browser`` contains it,
# and ``auth`` because ``author`` and ``authority`` do.
# ``pwd`` is substring-matched
# despite being short: 0.22.1's export template matched it that way, so token
# matching would stop redacting a fused name like ``dbpwd`` on regeneration,
# and no dictionary word contains it. ``pw`` stays token-matched -- it is inside
# far too many words. Accepted false positive: the shell's ``PWD`` and
# ``OLDPWD`` name a directory, not a password, and dictionary evidence cannot
# see shell vocabulary. Bare ``pwd`` was already credential-tier, so a
# parameter named ``oldpwd`` holding a path is refused in a sink by the same
# choice; OCTOWRIGHT_MACRO_CREDENTIAL_SINKS=allow is the opt-out.
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
        "pwd",
        "private_key",
        "secret",
        "token",
    }
)
CREDENTIAL_TOKEN_TOKENS = frozenset({"auth", "authentication", "bearer", "cookie", "cookies", "otp", "pw"})
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


def sensitive_value_variants(values: Iterable[str]) -> tuple[str, ...]:
    """Every spelling the given classified values can take in a rendered page.

    The public, multi-value form of `_serialized_variants`. A caller redacting a
    live DOM before a screenshot has to remove every encoding of every classified
    value and then assert nothing remains, which needs one flat set rather than a
    tuple per value.

    Ordered longest first, like `sensitive_arg_values` and `_serialized_variants`.
    That ordering is load-bearing for a replacing caller, not cosmetic: when one
    variant is a substring of another -- which percent-encoding routinely produces,
    since `quote` leaves short values unchanged -- replacing the shorter one first
    consumes the characters the longer match needed and leaves the rest of the
    longer spelling on the page.

    Non-string entries are skipped rather than raising: the values reach this from
    macro arguments, where a null field is ordinary, and `_serialized_variants`
    raises TypeError on one. An empty string needs no guard here -- that function
    already returns no variants for it, and an empty variant would match at every
    position.
    """
    variants: set[str] = set()
    for value in values:
        if isinstance(value, str):
            variants.update(_serialized_variants(value))
    return tuple(sorted(variants, key=len, reverse=True))


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


#: The session attribute that owns its scrub set, in the private namespace
#: composition roots already use (``_octowright_before_macro_action``).
SESSION_PRIVACY_LEDGER_ATTR = "_octowright_privacy_ledger"


class PrivacyLedger:
    """An append-only, de-duplicated set of values to scrub, longest first.

    Longest first because a replacing scrub must not let a shorter value consume
    the characters a longer one needed. De-duplicated because the scrub cost is
    per value per write, so it has to track distinct credentials, not runs.
    """

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._members: set[str] = set()
        self._values: tuple[str, ...] = ()
        self.add(values)

    def add(self, values: Iterable[str]) -> None:
        new = {value for value in values if isinstance(value, str) and value} - self._members
        if new:
            self._members |= new
            self._values = tuple(sorted(self._members, key=lambda value: (-len(value), value)))

    @property
    def values(self) -> tuple[str, ...]:
        return self._values


class SessionPrivacyLedger(PrivacyLedger):
    """A session's scrub set: appended to by every run and nested call, never cleared.

    Deliberately not restored at the run boundary. Recorder rows are driven by
    page events that outlive the run, and a credential typed in one sequence step
    keeps appearing in the next step's page-derived rows, so restoring would
    reopen cleartext rather than prevent stacking. Stacking is prevented by
    identity instead: exactly one ``SensitiveRecorder`` reads this ledger.
    """


class SensitiveRecorder:
    """Scrub macro values at the recorder boundary before any durable write.

    Holds a reference to the session ledger, not a copy of its values, so a
    value appended by a later run or a nested call is scrubbed from the very next
    write without installing anything.
    """

    def __init__(self, recorder: Any, ledger: PrivacyLedger) -> None:
        self._recorder = recorder
        self.ledger = ledger

    def _scrubbed(self, fields: dict[str, Any]) -> dict[str, Any]:
        values = self.ledger.values
        # Nothing to scrub is the common case for a session that never ran a
        # classified macro, and scrubbing an empty set still copies every field.
        return scrub_sensitive_values(fields, values) if values else fields

    def record(self, action: str, **fields: Any) -> None:
        self._recorder.record(action, **self._scrubbed(fields))

    def record_control(self, action: str, **fields: Any) -> None:
        self._recorder.record_control(action, **self._scrubbed(fields))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._recorder, name)


def session_privacy_ledger(session: Any) -> SessionPrivacyLedger:
    """The session's ledger, created on first use."""
    ledger = getattr(session, SESSION_PRIVACY_LEDGER_ATTR, None)
    # isinstance rather than ``is None``: a mock session answers every getattr.
    if not isinstance(ledger, SessionPrivacyLedger):
        ledger = SessionPrivacyLedger()
        setattr(session, SESSION_PRIVACY_LEDGER_ATTR, ledger)
    return ledger


def install_sensitive_recorder(session: Any, sensitive_values: Iterable[str] = ()) -> SessionPrivacyLedger:
    """Add values to the session's scrub set and make sure exactly one wrapper reads it.

    Idempotent and never uninstalled: a session that is already wrapped gets its
    ledger appended to, never a second wrapper. It wraps even when there is
    nothing to scrub yet, because a nested call or a later run may append a
    credential, and that append has to reach a ledger something reads.
    """
    ledger = session_privacy_ledger(session)
    ledger.add(sensitive_values)
    recorder = getattr(session, "recorder", None)
    if recorder is not None and not isinstance(recorder, SensitiveRecorder):
        session.recorder = SensitiveRecorder(recorder, ledger)
    return ledger
