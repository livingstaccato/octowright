# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""How many persistent values a session's scrub set holds before it is saturated (#248).

`privacy_ledger.SessionPrivacyLedger` is append-only and never cleared, and
every recorder write, console message, network row and socket URL is scrubbed
against it, so its cost grows with every distinct value it holds. A session
that admits many -- a looped sequence with generated passwords, many direct
password fills -- had no bound.

The cap **never drops or skips a value**: privacy wins over cost, so every value
that reaches the ledger is still added and scrubbed. What it does is mark the
session ``scrub_saturated`` (sticky, for the session's lifetime) once the
persistent count reaches it, and refuse a macro run that would add a persistent
value the ledger does not already hold -- before that run does anything. Only
persistent values count; a run-scoped value leaves when its run closes.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from provide.telemetry import get_logger

from octowright.request_errors import InvalidRequestError

log = get_logger(__name__)

SCRUB_MAX_VALUES_ENV = "OCTOWRIGHT_MACRO_SCRUB_MAX_VALUES"

#: Far above what a real session holds (a handful of credentials, a few typed
#: passwords) and low enough that a runaway loop is stopped long before the
#: per-write scrub becomes the session's dominant cost.
DEFAULT_SCRUB_MAX_VALUES = 256

#: Unparsable raw settings already warned about, so a bad value logs once per
#: process rather than on every ledger add.
_WARNED: set[str] = set()


def scrub_max_values(environ: Mapping[str, str] = os.environ) -> int:
    """The persistent-value cap; ``0`` means no cap.

    Unset or blank keeps the default. Anything that is not a non-negative
    integer also keeps the default, with a warning: a cap that refuses nothing
    is not a safe fallback for a typo, and a cap of zero would be.
    """
    raw = environ.get(SCRUB_MAX_VALUES_ENV, "").strip()
    if not raw:
        return DEFAULT_SCRUB_MAX_VALUES
    if raw.isascii() and raw.isdigit():
        return int(raw)
    if raw not in _WARNED:
        _WARNED.add(raw)
        log.warning(
            "octowright.macro.scrub_max_values_invalid",
            env=SCRUB_MAX_VALUES_ENV,
            default=DEFAULT_SCRUB_MAX_VALUES,
        )
    return DEFAULT_SCRUB_MAX_VALUES


def scrub_set_full(count: int, cap: int) -> InvalidRequestError:
    """The refusal for a run that would add to a full scrub set; never names a value."""
    return InvalidRequestError(
        f"this session's scrub set is full ({count} of {cap} persistent values), so a macro run that "
        "would add a new credential to it is refused before it runs. Relaunch the browser for a fresh "
        f"session, or raise {SCRUB_MAX_VALUES_ENV} (0 disables the cap)."
    )
