# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The last check before an artifact run bundle reaches disk (#248, Part 0).

The bundle (``result.json``, ``evidence.json``, ``summary.md``, a
``verification.json``) is scrubbed of what the run's privacy view holds. A
green bundle must not mean "policy never arrived", so two things are checked
here, after that scrub and before anything is written:

- **Unresolved.** A view that never resolved, was not sealed (its run had not
  finished admitting), or whose session's scrub set is saturated cannot vouch
  for the scrub. The bundle is still written -- nothing raises once the run
  directory exists -- with key-level redaction (`artifacts.redaction`) applied
  on top, and ``privacy_unresolved: true``.
- **Tripwire.** Every string, mapping keys included, is searched for each held
  value in every serialized spelling, ignoring case. The scrub matches case
  exactly (a page that upper-cases what it echoes slips past it); a match here
  is removed, case-insensitively, and the whole string is replaced if any of
  it is still found. ``privacy_tripwire: true`` says it happened.

Both flags name nothing; neither ever carries a value.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from provide.telemetry import get_logger

from octowright.artifacts.redaction import REDACTED_VALUE, redact_value
from octowright.macros.privacy_ledger import RunPrivacyLedger, session_scrub_set
from octowright.macros.scrub_engine import _identifier_bounded, _scrub_tree, sensitive_value_variants

log = get_logger(__name__)

UNRESOLVED_FIELD = "privacy_unresolved"
TRIPWIRE_FIELD = "privacy_tripwire"

#: Below this length a value is matched only on an alphanumeric boundary, as
#: the scrub matches it (`scrub_engine._WORD_BOUNDED_BELOW`).
_BOUNDED_BELOW = 4


@dataclass
class BundlePrivacy:
    """What one bundle write is checked against, and what the check found.

    ``values`` are matched anywhere (a macro's admitted arguments); ``bounded``
    only as whole identifiers (passwords the session saw typed, which are often
    ordinary words). ``flags`` is filled by `guard_documents`, and ``summary``
    by ``write_run_bundle`` with the summary as written, so a caller returns
    the same text.
    """

    values: tuple[str, ...] = ()
    bounded: frozenset[str] = frozenset()
    unresolved: bool = False
    flags: dict[str, bool] = field(default_factory=dict)
    summary: str | None = None


def _patterns(values: Iterable[str], bounded: frozenset[str]) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for value in sorted({*values, *bounded}, key=lambda item: (-len(item), item)):
        for variant in sensitive_value_variants([value]):
            if value in bounded and value not in values:
                text = _identifier_bounded(variant)
            elif len(value) < _BOUNDED_BELOW:
                text = rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])"
            else:
                text = re.escape(variant)
            patterns.append(re.compile(text, re.IGNORECASE))
    return patterns


def _tripwire(patterns: list[re.Pattern[str]], fired: list[bool]) -> Callable[[str], str]:
    present = re.compile("|".join(f"(?:{pattern.pattern})" for pattern in patterns), re.IGNORECASE).search

    def check(text: str) -> str:
        if not present(text):
            return text
        fired.append(True)
        for pattern in patterns:
            text = pattern.sub(REDACTED_VALUE, text)
        return REDACTED_VALUE if present(text) else text

    return check


def guard_documents(documents: dict[str, Any], privacy: BundlePrivacy) -> tuple[dict[str, Any], dict[str, bool]]:
    """*documents* as they may be written, and the flags to write with them.

    Never raises: a check that cannot run marks the bundle unresolved and
    leaves the key-level redaction in place.
    """
    flags: dict[str, bool] = {}
    unresolved = privacy.unresolved
    guarded = documents
    try:
        patterns = _patterns(privacy.values, privacy.bounded)
        if patterns:
            fired: list[bool] = []
            guarded = _scrub_tree(guarded, _tripwire(patterns, fired))
            if fired:
                flags[TRIPWIRE_FIELD] = True
                log.warning("octowright.artifacts.privacy_tripwire", documents=sorted(documents))
    except Exception as exc:  # the bundle must still be written; say why it is unresolved
        log.warning("octowright.artifacts.privacy_check_failed", error_type=type(exc).__name__)
        unresolved = True
    if unresolved:
        guarded = {name: redact_value(document) for name, document in guarded.items()}
        flags[UNRESOLVED_FIELD] = True
    privacy.flags.update(flags)
    return guarded, flags


def bundle_guard(session: Any, run_ledger: RunPrivacyLedger) -> BundlePrivacy:
    """What an artifact run's bundle is checked against: the run's view and the session's set.

    Unresolved when the view admitted no site, was not sealed, or the
    session's scrub set is saturated. The session's typed passwords are
    matched only as whole identifiers, as the recording matches them.
    """
    anywhere, bounded = session_scrub_set(session)
    run_values = run_ledger.values
    return BundlePrivacy(
        values=tuple(sorted({*run_values, *anywhere})),
        bounded=frozenset(bounded - set(run_values)),
        unresolved=not run_ledger.sealed or not run_ledger.resolved_sites or run_ledger.saturated,
    )
