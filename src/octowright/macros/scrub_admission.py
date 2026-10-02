# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Which identity/contextual macro values are too short or too common to blind-scrub (#247).

A blind scrub replaces a value wherever it appears in a string, with no
provenance. For a credential that is the point, at any length. For an identity
or contextual value it is a guess, and a short or common one guesses wrong:
``session="1"`` rewrote ``li:nth-child(1)`` and ``?page=1``, ``user="admin"``
rewrote the word "admin" in unrelated prose, and because the session ledger is
never cleared every later row on the session was rewritten too. So such a value
is exempt from blind scrubbing (it is still redacted from its own argument
field, structurally) and the run result names it by path.

Never consulted for a credential-tier value: callers apply this to the identity
and contextual tiers only, so a short password is never left in cleartext.

Live replay (``macros.privacy``) and the exported macro CLI share this module:
the exporter renders its source verbatim into every generated script
(``artifacts.script_export.render_macro_cli``), the way it renders
``credential_sinks``, so it imports only the standard library and a value live
replay exempts is exempted by the script too. Every name it defines lands in the
script's namespace, hence the ``SCRUB_`` prefixes.

Every unparsable setting falls back to the side that scrubs MORE: a typo must
never be what leaves a value visible.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

SCRUB_MIN_LENGTH_ENV = "OCTOWRIGHT_MACRO_SCRUB_MIN_LENGTH"
SCRUB_COMMON_VALUES_ENV = "OCTOWRIGHT_MACRO_SCRUB_COMMON_VALUES"
SCRUB_RUN_SCOPED_ENV = "OCTOWRIGHT_MACRO_SCRUB_RUN_SCOPED"

#: Below four characters a value is a small number, an initial or a two-letter
#: code -- ``1``, ``42``, ``999``, ``id``, ``en`` -- and those are exactly the
#: strings that also occur in selectors (``nth-child(1)``), query strings
#: (``?page=1``) and prose. Four is also the boundary the scrub engine already
#: draws (``scrub_engine._WORD_BOUNDED_BELOW``) below which a value only
#: matches as a whole token, because shorter ones collide too often. A real
#: email, phone number or username is longer.
DEFAULT_SCRUB_MIN_LENGTH = 4

#: Placeholder identities that name a role or a fixture, not a person, and that
#: also occur as ordinary words in pages, selectors and routes (``#admin-menu``,
#: ``/user/settings``, "Guest checkout"). Small on purpose: every entry is a
#: value an operator could genuinely want hidden, so the list holds only the
#: shared test-account vocabulary. Compared ignoring case and surrounding space.
DEFAULT_SCRUB_COMMON_VALUES = frozenset(
    {
        "admin",
        "administrator",
        "anonymous",
        "default",
        "demo",
        "example",
        "guest",
        "null",
        "none",
        "root",
        "test",
        "tester",
        "user",
    }
)

_SCRUB_TRUE_TOKENS = frozenset({"", "1", "on", "true", "yes", "always"})


def scrub_min_length(environ: Mapping[str, str] = os.environ) -> int:
    """Identity/contextual values shorter than this are not blind-scrubbed; 0 disables the floor.

    Unset or blank keeps the default. Anything that is not a non-negative
    integer disables the floor rather than guessing one.
    """
    raw = environ.get(SCRUB_MIN_LENGTH_ENV, "").strip()
    if not raw:
        return DEFAULT_SCRUB_MIN_LENGTH
    if not (raw.isascii() and raw.isdigit()):
        return 0
    return int(raw)


def scrub_common_values(environ: Mapping[str, str] = os.environ) -> frozenset[str]:
    """The common-value list: unset keeps the default, ``+a,b`` extends it, ``a,b`` replaces it.

    Comma-separated, compared casefolded. An empty value (or one of only
    commas) replaces the list with nothing, which disables it.
    """
    raw = environ.get(SCRUB_COMMON_VALUES_ENV)
    if raw is None:
        return DEFAULT_SCRUB_COMMON_VALUES
    text = raw.strip()
    base: frozenset[str] = frozenset()
    if text.startswith("+"):
        base = DEFAULT_SCRUB_COMMON_VALUES
        text = text[1:]
    listed = {item.strip().casefold() for item in text.split(",")}
    return base | frozenset(item for item in listed if item)


def scrub_run_scoped(environ: Mapping[str, str] = os.environ) -> bool:
    """Whether identity/contextual values are scrubbed for their own run only (default on).

    Off makes them join the session-wide ledger, as credentials do. An unknown
    value means off: session-wide scrubs more.
    """
    raw = environ.get(SCRUB_RUN_SCOPED_ENV, "").strip().lower()
    return raw in _SCRUB_TRUE_TOKENS


def scrub_exemption_reason(
    value: str,
    *,
    min_length: int | None = None,
    common: frozenset[str] | None = None,
) -> str | None:
    """``"short"``, ``"common"``, or None when an identity/contextual *value* is blind-scrubbed."""
    floor = scrub_min_length() if min_length is None else min_length
    if len(value) < floor:
        return "short"
    listed = scrub_common_values() if common is None else common
    if value.strip().casefold() in listed:
        return "common"
    return None
