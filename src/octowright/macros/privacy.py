# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Versioned privacy rules shared by macro execution and generated exports."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Literal

from octowright.macros.nesting import iter_nested_actions
from octowright.macros.redaction_text import normalize

# The scrub itself (variants, patterns, the structure walk) lives in
# ``scrub_engine``; re-exported so every existing import keeps its spelling.
from octowright.macros.scrub_engine import _MAX_ENCODING_DEPTH as _MAX_ENCODING_DEPTH
from octowright.macros.scrub_engine import REDACTED as REDACTED
from octowright.macros.scrub_engine import _scrub_patterns as _scrub_patterns
from octowright.macros.scrub_engine import _scrub_text as _scrub_text
from octowright.macros.scrub_engine import _scrub_tree as _scrub_tree
from octowright.macros.scrub_engine import _serialized_variants as _serialized_variants
from octowright.macros.scrub_engine import filtered_text_scrubber
from octowright.macros.scrub_engine import scrub_sensitive_values as scrub_sensitive_values
from octowright.macros.scrub_engine import sensitive_value_variants as sensitive_value_variants

ARG_PRIVACY_CLASSIFIER_VERSION = 5
BLIND_SCRUB_POLICY_ENV = "OCTOWRIGHT_MACRO_BLIND_SCRUB_POLICY"

BlindScrubPolicy = Literal["credentials", "all", "reject"]
PrivacyTier = Literal["credential", "identity", "contextual"]


@dataclass(frozen=True)
class ClassifiedArgValue:
    """One classified leaf, retaining the provenance blind scrubbing loses."""

    value: str
    path: str
    tier: PrivacyTier


class MacroBlindScrubRejected(ValueError):
    """The configured policy refuses a non-credential classified argument."""


# Three classifiers decided sensitivity independently -- this one,
# ``artifacts.redaction.is_sensitive_key`` and
# ``macros.substitution.is_credential_arg`` -- and disagreed on 1173 of 1916
# sampled names. The disagreements were holes, not opinions: ``private_key``,
# ``cookie`` and ``set_cookie`` reached args_used and generated exports in
# cleartext because only the redaction classifier knew them, ``otp`` was known
# only to the sink guard, and plural forms bypassed all three.
#
# The vocabulary below is the UNION of what all three matched before it (at b3dafe83), frozen
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


def _matches(
    key: object,
    substrings: frozenset[str],
    whole_tokens: frozenset[str],
    *,
    pairs: frozenset[tuple[str, str]] = frozenset(),
) -> bool:
    normalized = _normalized_key(key)
    if any(token in normalized for token in substrings):
        return True
    tokens = key_tokens(key)
    if _token_candidates(tokens) & whole_tokens:
        return True
    return bool(set(pairwise(tokens)).intersection(pairs))


def is_credential_key(key: object) -> bool:
    """The tier that gates the credential sink guard, not merely redaction.

    Identity is deliberately excluded: ``{{order_id}}`` and an email in a URL are
    the ordinary parameterized-navigation case, while a password there is
    exfiltration.
    """
    return _matches(
        key,
        CREDENTIAL_SUBSTRING_TOKENS,
        CREDENTIAL_TOKEN_TOKENS,
        pairs=SENSITIVE_KEY_PAIRS,
    )


def is_sensitive_arg_key(key: object) -> bool:
    return _matches(key, SUBSTRING_TOKENS, TOKEN_TOKENS, pairs=SENSITIVE_KEY_PAIRS)


def _privacy_tier(key: object) -> PrivacyTier | None:
    if is_credential_key(key):
        return "credential"
    if _matches(key, frozenset(), IDENTITY_TOKEN_TOKENS):
        return "identity"
    if _matches(key, frozenset(), CONTEXTUAL_TOKEN_TOKENS):
        return "contextual"
    return None


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


_TIER_RANK: dict[PrivacyTier, int] = {"contextual": 1, "identity": 2, "credential": 3}


def _stronger_tier(inherited: PrivacyTier | None, own: PrivacyTier | None) -> PrivacyTier | None:
    if inherited is None:
        return own
    if own is None or _TIER_RANK[inherited] >= _TIER_RANK[own]:
        return inherited
    return own


def _child_path(path: str, key: object) -> str:
    rendered = str(key)
    if is_field_name(rendered):
        return f"{path}.{rendered}" if path else rendered
    # A non-field mapping key is data under a classified branch. Do not put
    # that data into a rejection message disguised as a path component.
    return f"{path}[<key>]"


def _collect_classified_mapping(
    value: Mapping[Any, Any],
    *,
    inherited: PrivacyTier | None,
    path: str,
) -> set[ClassifiedArgValue]:
    values: set[ClassifiedArgValue] = set()
    for key, item in value.items():
        if inherited is not None and _is_identity_key(key):
            values.add(ClassifiedArgValue(str(key), f"{path}[<key>]", inherited))
        branch_tier = _stronger_tier(inherited, _privacy_tier(key))
        values.update(
            _collect_classified_values(
                item,
                inherited=branch_tier,
                path=_child_path(path, key),
            )
        )
    return values


def _collect_classified_sequence(
    value: Iterable[Any],
    *,
    inherited: PrivacyTier | None,
    path: str,
) -> set[ClassifiedArgValue]:
    values: set[ClassifiedArgValue] = set()
    for index, item in enumerate(value):
        values.update(_collect_classified_values(item, inherited=inherited, path=f"{path}[{index}]"))
    return values


def _collect_classified_values(
    value: Any,
    *,
    inherited: PrivacyTier | None,
    path: str,
) -> set[ClassifiedArgValue]:
    if isinstance(value, Mapping):
        return _collect_classified_mapping(value, inherited=inherited, path=path)
    if isinstance(value, (list, tuple)):
        return _collect_classified_sequence(value, inherited=inherited, path=path)
    if isinstance(value, (set, frozenset)):
        return _collect_classified_sequence(sorted(value, key=repr), inherited=inherited, path=path)
    if inherited is not None and value not in (None, ""):
        return {ClassifiedArgValue(str(value), path, inherited)}
    return set()


#: The ``{{name}}`` placeholder grammar: what ``substitution.substitute``
#: expands, what the linters and this module read argument names with, and
#: (rendered as the string) what an exported macro CLI substitutes. Defined
#: here rather than in ``substitution`` because that module imports this one.
PLACEHOLDER_PATTERN = r"\{\{([^}]+)\}\}"
PLACEHOLDER_RE = re.compile(PLACEHOLDER_PATTERN)


def assertion_text_args(actions: Any) -> frozenset[str]:
    """Names of the arguments substituted into an ``expect_no_text`` ``text``, anywhere in *actions*.

    Such an argument is the forbidden text itself -- a national id, a card
    number, a password -- whatever it is named, so it is classified sensitive by
    POSITION where every other argument is classified by name. Live replay
    (``args_used`` and the scrub set) and the exported CLI both read it from
    here, which is what keeps the two from disagreeing about the same macro.
    A fill/type argument is deliberately not included: feeding a fill says
    nothing about the value (``qty``, ``order_id``), and a password field's fill
    is already redacted by the recorder's input classification.
    """
    # A called macro's own assertions are classified where that call runs,
    # against the arguments it is called with, so calls are not followed.
    return frozenset(
        name
        for action in iter_nested_actions(actions)
        if action.get("action") == "expect_no_text" and isinstance(action.get("text"), str)
        for name in PLACEHOLDER_RE.findall(action["text"])
    )


def classified_arg_values(args: Mapping[str, Any]) -> tuple[ClassifiedArgValue, ...]:
    """Classified leaves by NAME alone; see `MacroArgPrivacy` for a macro's own arguments."""
    return NAME_ONLY_PRIVACY.classified(args)


def sensitive_arg_values(args: Mapping[str, Any]) -> tuple[str, ...]:
    """Every classified value, independent of blind-scrub admission policy."""
    return tuple(sorted({item.value for item in classified_arg_values(args)}, key=lambda value: (-len(value), value)))


def blind_scrub_policy() -> BlindScrubPolicy:
    """Resolve the strict policy governing provenance-free value replacement."""
    raw = os.environ.get(BLIND_SCRUB_POLICY_ENV, "credentials").strip().lower()
    if raw == "credentials":
        return "credentials"
    if raw == "all":
        return "all"
    if raw == "reject":
        return "reject"
    raise ValueError(f"{BLIND_SCRUB_POLICY_ENV} must be 'credentials', 'all', or 'reject'")


def _reject_noncredential_values(classified: tuple[ClassifiedArgValue, ...]) -> None:
    rejected = [item for item in classified if item.tier != "credential"]
    if not rejected:
        return
    details = ", ".join(sorted({f"{item.path} ({item.tier})" for item in rejected}))
    raise MacroBlindScrubRejected(f"non-credential classified arguments refused: {details}")


def _admitted_classified_values(
    classified: tuple[ClassifiedArgValue, ...],
    policy: BlindScrubPolicy,
) -> tuple[ClassifiedArgValue, ...]:
    if policy == "reject":
        _reject_noncredential_values(classified)
    if policy == "all":
        return classified
    return tuple(item for item in classified if item.tier == "credential")


def blind_scrub_arg_values(args: Mapping[str, Any], *, policy: BlindScrubPolicy | None = None) -> tuple[str, ...]:
    """Values admitted to blind scrubbers, by NAME alone; see `MacroArgPrivacy`."""
    return NAME_ONLY_PRIVACY.blind_scrub(args, policy=policy)


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
    """*args* with sensitive values redacted, by NAME alone; see `MacroArgPrivacy`."""
    return NAME_ONLY_PRIVACY.redact(args, marker=marker)


@dataclass(frozen=True)
class MacroArgPrivacy:
    """How one macro's arguments are classified: by name, and by position.

    An argument substituted into an ``expect_no_text`` is the forbidden text
    itself, so it is credential-tier whatever it is named (`assertion_text_args`).
    That fact belongs to the macro, not to the arguments, and it used to be a
    keyword threaded through every classifier with an empty default -- so a
    caller that forgot it silently leaked the forbidden text. Build the view
    once per macro with `for_macro` and ask it; the module-level
    `redact_args`/`blind_scrub_arg_values`/`classified_arg_values` are the
    name-only view, for records that no longer know their macro.
    """

    assertion_args: frozenset[str] = frozenset()

    @classmethod
    def for_macro(cls, actions: Any) -> MacroArgPrivacy:
        return cls(assertion_text_args(actions))

    def _tier(self, key: object) -> PrivacyTier | None:
        return "credential" if key in self.assertion_args else _privacy_tier(key)

    def classified(self, args: Mapping[str, Any]) -> tuple[ClassifiedArgValue, ...]:
        """Classified leaves with their effective tier and value-free-safe path."""
        values: set[ClassifiedArgValue] = set()
        for key, value in args.items():
            values.update(_collect_classified_values(value, inherited=self._tier(key), path=str(key)))
        return tuple(sorted(values, key=lambda item: (item.path, -_TIER_RANK[item.tier], item.value)))

    def blind_scrub(self, args: Mapping[str, Any], *, policy: BlindScrubPolicy | None = None) -> tuple[str, ...]:
        """Values admitted to blind scrubbers under the configured policy."""
        selected = _admitted_classified_values(self.classified(args), policy or blind_scrub_policy())
        return tuple(sorted({item.value for item in selected}, key=lambda value: (-len(value), value)))

    def redact(self, args: Mapping[str, Any], *, marker: str = REDACTED) -> dict[str, Any]:
        """*args* for a response or a log: sensitive keys replaced, admitted values scrubbed."""
        redacted = {
            str(key): marker
            if is_sensitive_arg_key(key) or key in self.assertion_args
            else _redact_nested_args(value, marker)
            for key, value in args.items()
        }
        policy = blind_scrub_policy()
        # ``reject`` governs macro INVOCATIONS, not read-only rendering of an
        # existing manifest or result. Structural redaction must remain usable in
        # that mode, with the same alias handling as the replay-safe default.
        blind_policy: BlindScrubPolicy = "credentials" if policy == "reject" else policy
        return scrub_sensitive_values(redacted, self.blind_scrub(args, policy=blind_policy), marker=marker)


#: The view for arguments whose macro is not known.
NAME_ONLY_PRIVACY = MacroArgPrivacy()


#: Keys the ``expect_no_text`` recording digest. Random per process and never
#: written anywhere, so a digest in a JSONL on disk cannot be brute-forced
#: offline against a dictionary of likely passwords; the cost is that only the
#: daemon that made a recording can bind it (see `assertion_text_digest`).
_ASSERTION_DIGEST_KEY = secrets.token_bytes(32)


def assertion_text_digest(text: str) -> str:
    """A keyed digest of *text* as ``expect_no_text`` compares it.

    ``expect_no_text`` records its text as a marker, never the text, because it
    is usually a secret. ``save_macro`` then needs to know WHICH declared
    parameter a marker stood for -- binding every marker to the one credential
    parameter turned a recorded ``expect_no_text('Traceback')`` into a password
    check. The recorder writes this digest beside the marker and save binds a
    marker only to a parameter whose value digests the same. Normalised with
    ``redaction_text.normalize``, the comparison the check itself uses, so a
    parameter spelled with different case or invisible characters still binds.
    After a daemon restart the key is new, nothing matches, and the marker is
    left for the author to fill in (``macro_lint`` reports it).
    """
    return hmac.new(_ASSERTION_DIGEST_KEY, normalize(text).encode("utf-8"), hashlib.sha256).hexdigest()


def assertion_digest_matches(value: object, digest: object) -> bool:
    if not isinstance(value, str) or not value or not isinstance(digest, str):
        return False
    return hmac.compare_digest(assertion_text_digest(value), digest)


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
        self._bounded: set[str] = set()
        self._anywhere: set[str] = set()
        self._word_bounded: frozenset[str] = frozenset()
        # Bumped by every add that changes what a scrub does; `_text_scrubber`
        # rebuilds its cache when this moves.
        self._version = 0
        self._cached: tuple[int, Callable[[str], str]] | None = None
        self.add(values)

    def add(self, values: Iterable[str], *, word_bounded: bool = False) -> None:
        """Append *values*; ``word_bounded`` ones are scrubbed only as whole identifiers.

        A value admitted both ways is scrubbed anywhere: the stronger claim wins,
        whichever order the two admissions arrived in.
        """
        admitted = {value for value in values if isinstance(value, str) and value}
        claim = self._bounded if word_bounded else self._anywhere
        if admitted <= claim:
            return
        claim.update(admitted)
        new = admitted - self._members
        if new:
            self._members |= new
            self._values = tuple(sorted(self._members, key=lambda value: (-len(value), value)))
        self._word_bounded = frozenset(self._bounded - self._anywhere)
        self._version += 1

    @property
    def values(self) -> tuple[str, ...]:
        return self._values

    @property
    def word_bounded(self) -> frozenset[str]:
        """Values scrubbed only where not embedded in a longer identifier.

        The passwords `admit_redacted_input` adds. They are whatever someone
        typed into a password field -- very often a test value such as
        ``admin`` -- and replacing one anywhere rewrote ``administrator`` and
        ``#admin-menu`` in every later row, so a macro saved from the recording
        replayed broken selectors. Bounding keeps every echo that stands as its
        own token (``pw=admin``, ``"admin"``, a URL parameter) scrubbed; what
        it gives up is an echo glued to other identifier characters.
        """
        return self._word_bounded

    def scrub(self, value: Any) -> Any:
        """*value* scrubbed of this ledger's values, or *value* itself when it is empty.

        Byte-identical to `scrub_sensitive_values` over the same state, which
        the live buffers (`input_redaction.live_scrubbed`) call on every
        console message, network row and socket URL: see `_text_scrubber`.
        """
        return _scrub_tree(value, self._text_scrubber()) if self._values else value

    def _text_scrubber(self) -> Callable[[str], str]:
        """The per-string scrub for the current state, built once per state.

        Almost every string a page produces holds no ledger value; see
        `scrub_engine.filtered_text_scrubber`.
        """
        cached = self._cached
        if cached is not None and cached[0] == self._version:
            return cached[1]
        scrub_text = filtered_text_scrubber(self._values, self._word_bounded)
        self._cached = (self._version, scrub_text)
        return scrub_text


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
        # Nothing to scrub is the common case for a session that never admitted a
        # macro value, and scrubbing an empty set still copies every field --
        # ``PrivacyLedger.scrub`` returns *fields* untouched then.
        scrubbed: dict[str, Any] = self.ledger.scrub(fields)
        return scrubbed

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


def with_session_ledger(session: Any, values: Iterable[str]) -> PrivacyLedger:
    """*values* merged with the session ledger, which keeps each value's word-bounded status.

    For a caller that must redact everything the session holds, not only its
    own run's values: the ledger also carries what was admitted outside any
    macro -- a password the input classification hid from a direct
    ``browser_fill`` -- and the page may still render it. Reads the ledger
    without creating one.

    A ledger, not a flat tuple: a flat tuple scrubbed a typed ``admin`` inside
    ``Administrator`` and ``#admin-menu`` again, which is what
    `PrivacyLedger.word_bounded` exists to prevent. *values* are the run's own
    classified values and match anywhere, so a value held both ways is
    scrubbed anywhere (`PrivacyLedger.add`).
    """
    merged = PrivacyLedger(values)
    ledger = getattr(session, SESSION_PRIVACY_LEDGER_ATTR, None)
    if isinstance(ledger, SessionPrivacyLedger):
        bounded = ledger.word_bounded
        merged.add(value for value in ledger.values if value not in bounded)
        merged.add(bounded, word_bounded=True)
    return merged


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
    # The markdown cache is the other durable write of page content; a page
    # that renders the password would otherwise put it on disk in cleartext.
    if session.durable_text_scrubber is None:
        session.durable_text_scrubber = DurableTextScrubber(ledger)
    return ledger


class DurableTextScrubber:
    """The session's ``durable_text_scrubber``: scrubs page text against its ledger.

    ``active`` lets a caller with work to do BEFORE scrubbing skip it: the
    websocket sidecar base64-decodes every binary frame to look for a value,
    and a session that once ran a macro keeps this installed with a ledger that
    may well be empty.
    """

    def __init__(self, ledger: PrivacyLedger) -> None:
        self.ledger = ledger

    @property
    def active(self) -> bool:
        return bool(self.ledger.values)

    def __call__(self, text: str) -> str:
        scrubbed: str = self.ledger.scrub(text)
        return scrubbed

    def scrub_value(self, value: Any) -> Any:
        """A structure (a console entry, a network row) scrubbed like text."""
        return self.ledger.scrub(value)


def admit_redacted_input(session: Any, value: str) -> None:
    """A value the recorder's input classification hid, now kept out of every other durable write.

    ``OCTOWRIGHT_REDACT_INPUTS`` replaces a password field's typed value in the
    ``fill``/``type`` row, but the page is free to echo it -- a
    ``console.log``, a request body, a websocket frame, the rendered page --
    and each of those rows used to persist it in cleartext. Appending to the
    session ledger (never replacing it) keeps it scrubbed across every later
    macro run as well. Admitted WORD-BOUNDED (`PrivacyLedger.word_bounded`),
    unlike a macro's classified values: only the field type says this is a
    secret, and a typed password is often an ordinary word.
    """
    install_sensitive_recorder(session).add([value], word_bounded=True)
