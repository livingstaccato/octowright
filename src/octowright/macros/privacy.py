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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any, Literal

from octowright.macros.nesting import iter_nested_actions

# The scrub sets live in ``privacy_ledger``; re-exported so every existing
# import keeps its spelling.
from octowright.macros.privacy_ledger import SESSION_PRIVACY_LEDGER_ATTR as SESSION_PRIVACY_LEDGER_ATTR
from octowright.macros.privacy_ledger import DurableTextScrubber as DurableTextScrubber
from octowright.macros.privacy_ledger import PrivacyLedger as PrivacyLedger
from octowright.macros.privacy_ledger import RunPrivacyLedger as RunPrivacyLedger
from octowright.macros.privacy_ledger import SensitiveRecorder as SensitiveRecorder
from octowright.macros.privacy_ledger import SessionPrivacyLedger as SessionPrivacyLedger
from octowright.macros.privacy_ledger import admit_call_privacy as admit_call_privacy
from octowright.macros.privacy_ledger import admit_redacted_input as admit_redacted_input
from octowright.macros.privacy_ledger import install_sensitive_recorder as install_sensitive_recorder
from octowright.macros.privacy_ledger import scrub_with_session as scrub_with_session
from octowright.macros.privacy_ledger import refuse_if_scrub_set_full as refuse_if_scrub_set_full
from octowright.macros.privacy_ledger import run_privacy_ledger as run_privacy_ledger
from octowright.macros.privacy_ledger import scrub_saturation_fields as scrub_saturation_fields
from octowright.macros.privacy_ledger import session_privacy_ledger as session_privacy_ledger
from octowright.macros.privacy_ledger import with_session_values as with_session_values
from octowright.macros.redaction_text import normalize
from octowright.macros.scrub_admission import (
    scrub_common_values,
    scrub_exemption_reason,
    scrub_min_length,
    scrub_run_scoped,
)

# The scrub itself (variants, patterns, the structure walk) lives in
# ``scrub_engine``; re-exported so every existing import keeps its spelling.
from octowright.macros.scrub_engine import _MAX_ENCODING_DEPTH as _MAX_ENCODING_DEPTH
from octowright.macros.scrub_engine import REDACTED as REDACTED
from octowright.macros.scrub_engine import _scrub_patterns as _scrub_patterns
from octowright.macros.scrub_engine import _scrub_text as _scrub_text
from octowright.macros.scrub_engine import _scrub_tree as _scrub_tree
from octowright.macros.scrub_engine import _serialized_variants as _serialized_variants
from octowright.macros.scrub_engine import scrub_sensitive_values as scrub_sensitive_values
from octowright.macros.scrub_engine import sensitive_value_variants as sensitive_value_variants

# The ``{{name}}`` placeholder grammar lives in ``octowright.placeholders``
# (shared with scenario templates); re-exported so every macro import keeps
# its spelling.
from octowright.placeholders import PLACEHOLDER_PATTERN as PLACEHOLDER_PATTERN
from octowright.placeholders import PLACEHOLDER_RE as PLACEHOLDER_RE

ARG_PRIVACY_CLASSIFIER_VERSION = 7
BLIND_SCRUB_POLICY_ENV = "OCTOWRIGHT_MACRO_BLIND_SCRUB_POLICY"

BlindScrubPolicy = Literal["credentials", "all", "reject"]
PrivacyTier = Literal["credential", "identity", "contextual"]


@dataclass(frozen=True)
class ClassifiedArgValue:
    """One classified leaf, retaining the provenance blind scrubbing loses."""

    value: str
    path: str
    tier: PrivacyTier


@dataclass(frozen=True)
class ScrubExemption:
    """An identity/contextual argument left out of blind scrubbing (#247): never its value."""

    path: str
    tier: PrivacyTier
    reason: str  # "short" or "common", see `scrub_admission`

    def as_dict(self, macro: str | None = None) -> dict[str, str]:
        row = {"path": self.path, "tier": self.tier, "reason": self.reason}
        return {"macro": macro, **row} if macro is not None else row


def _longest_first(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values), key=lambda value: (-len(value), value)))


@dataclass(frozen=True)
class ScrubAdmission:
    """What the blind-scrub policy admitted for one set of arguments, and for how long.

    ``persistent`` values join the session-wide ledger: every credential, and
    identity/contextual values when ``OCTOWRIGHT_MACRO_SCRUB_RUN_SCOPED`` is
    off. ``run_scoped`` values are scrubbed for the supplying run only.
    ``exempt`` names what the floor or the common-value list left visible.
    ``sources`` is where each admitted value came from (its argument path and
    tier), so a refused screenshot can say which argument it rendered; it holds
    the values, so it is left out of ``repr`` and comparison.
    """

    persistent: tuple[str, ...] = ()
    run_scoped: tuple[str, ...] = ()
    exempt: tuple[ScrubExemption, ...] = ()
    sources: tuple[ClassifiedArgValue, ...] = field(default=(), repr=False, compare=False)

    @property
    def values(self) -> tuple[str, ...]:
        """Everything admitted, longest first: what this run's own scrubbers use."""
        return _longest_first((*self.persistent, *self.run_scoped))


def _scrub_admission(admitted: tuple[ClassifiedArgValue, ...]) -> ScrubAdmission:
    credentials = {item.value for item in admitted if item.tier == "credential"}
    others = [item for item in admitted if item.value not in credentials]
    if not others:
        return ScrubAdmission(persistent=_longest_first(credentials))
    min_length, common = scrub_min_length(), scrub_common_values()
    exempt: list[ScrubExemption] = []
    scoped: set[str] = set()
    for item in others:
        reason = scrub_exemption_reason(item.value, min_length=min_length, common=common)
        if reason is None:
            scoped.add(item.value)
        else:
            exempt.append(ScrubExemption(item.path, item.tier, reason))
    if not scrub_run_scoped():
        credentials |= scoped
        scoped = set()
    return ScrubAdmission(
        persistent=_longest_first(credentials),
        run_scoped=_longest_first(scoped),
        exempt=tuple(sorted(exempt, key=lambda item: item.path)),
    )


def _with_sources(admission: ScrubAdmission, admitted: tuple[ClassifiedArgValue, ...]) -> ScrubAdmission:
    """*admission* carrying the provenance of every value it kept."""
    kept = set(admission.values)
    return replace(admission, sources=tuple(item for item in admitted if item.value in kept))


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
    # A boolean carries no secret, and ``str(True)`` in a case-insensitive
    # ledger would rewrite every true/false on the session. A number stays:
    # ``{{otp}}`` expands to its digits. The key still redacts it structurally.
    if inherited is not None and value not in (None, "") and not isinstance(value, bool):
        return {ClassifiedArgValue(str(value), path, inherited)}
    return set()


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
    #: Args that hold a credential whatever they are named: a sequence's
    #: ``{"credential": ...}`` arguments (``run_macro``'s *credential_args*),
    #: and the ones the macro's own ``parameter_specs`` declare sensitive.
    #: Name classification alone cannot see them.
    credential_args: frozenset[str] = frozenset()
    #: Top-level args the macro's ``parameter_specs`` declare not sensitive,
    #: already held to the floor (`parameter_specs.resolve_macro_privacy`): an
    #: identity or contextual name the macro says is meant to be shown. Never
    #: a credential-named, credential-origin or positional one. Keys nested
    #: under it are still classified by their own names.
    public_args: frozenset[str] = frozenset()

    @classmethod
    def for_macro(cls, actions: Any, *, credential_args: frozenset[str] = frozenset()) -> MacroArgPrivacy:
        """By name and position only; `parameter_specs.resolve_macro_privacy` adds the macro's declarations."""
        return cls(assertion_text_args(actions), frozenset(credential_args))

    def _positional(self, key: object) -> bool:
        return key in self.assertion_args or key in self.credential_args

    def _public(self, key: object) -> bool:
        # The floor again, so a view built by hand cannot unmark a credential either.
        return key in self.public_args and not self._positional(key) and not is_credential_key(key)

    def _tier(self, key: object) -> PrivacyTier | None:
        if self._positional(key):
            return "credential"
        return None if self._public(key) else _privacy_tier(key)

    def tier_of(self, key: object) -> PrivacyTier | None:
        """The tier a top-level argument named *key* resolves to here; ``None`` when it is not classified."""
        return self._tier(key)

    def classified(self, args: Mapping[str, Any]) -> tuple[ClassifiedArgValue, ...]:
        """Classified leaves with their effective tier and value-free-safe path."""
        values: set[ClassifiedArgValue] = set()
        for key, value in args.items():
            values.update(_collect_classified_values(value, inherited=self._tier(key), path=str(key)))
        return tuple(sorted(values, key=lambda item: (item.path, -_TIER_RANK[item.tier], item.value)))

    def admission(self, args: Mapping[str, Any], *, policy: BlindScrubPolicy | None = None) -> ScrubAdmission:
        """What the configured policy admits to blind scrubbers, split by how long it is scrubbed.

        A credential-tier value is admitted at any length and session-wide. An
        identity/contextual one (admitted only under ``all``) is left out when
        it is short or common (`scrub_admission`), and is otherwise scrubbed for
        the supplying run only unless run scoping is off.
        """
        admitted = _admitted_classified_values(self.classified(args), policy or blind_scrub_policy())
        return _with_sources(_scrub_admission(admitted), admitted)

    def blind_scrub(self, args: Mapping[str, Any], *, policy: BlindScrubPolicy | None = None) -> tuple[str, ...]:
        """Values admitted to blind scrubbers under the configured policy."""
        return self.admission(args, policy=policy).values

    def redact(self, args: Mapping[str, Any], *, marker: str = REDACTED) -> dict[str, Any]:
        """*args* for a response or a log: sensitive keys replaced, admitted values scrubbed."""
        redacted = {
            str(key): marker
            if (is_sensitive_arg_key(key) and not self._public(key)) or self._positional(key)
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
