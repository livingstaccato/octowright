# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a ``fill``/``type`` row records for a typed value: the one decision.

The selector actions (``core_page_mixin``) and the semantic-locator actions
(``core_locator_mixin``) each probe their target element differently, but what
they do with the answer is one rule, and it had been written twice with the
``OCTOWRIGHT_REDACT_INPUTS`` mode parsed two different ways.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from octowright.defaults import REDACTED_INPUT_PLACEHOLDER
from octowright.session.aria_redaction import resolve_redaction_mode

#: Read both el.autocomplete (the IDL property -- only present on form-control
#: elements) and el.getAttribute('autocomplete') (the raw attribute -- present
#: on any element that declares it). Custom elements / <div contenteditable>
#: declare autocomplete via the attribute only.
CREDENTIAL_FIELD_JS = (
    "el => el ? {"
    "  type: el.type ? String(el.type).toLowerCase() : '',"
    "  ac: el.autocomplete ? String(el.autocomplete).toLowerCase() : ''"
    "    || (el.getAttribute && el.getAttribute('autocomplete')"
    "         ? String(el.getAttribute('autocomplete')).toLowerCase() : '')"
    "} : {type: '', ac: ''}"
)

_CREDENTIAL_AUTOCOMPLETE = frozenset({"current-password", "new-password", "one-time-code"})


def classify_credential_field(info: Any) -> bool | None:
    """Whether a `CREDENTIAL_FIELD_JS` result names a credential field.

    ``None`` when *info* is not the ``{type, ac}`` shape the probe returns: the
    field could not be classified, which `recorded_input_value` redacts.
    """
    if not isinstance(info, dict):
        return None
    if info.get("type") == "password":
        return True
    return info.get("ac") in _CREDENTIAL_AUTOCOMPLETE


def probe_timeout_ms(deadline: float) -> int:
    """What is left of a step's *deadline* (``time.monotonic()``) for the redaction probe, at least 1ms.

    The probe classifies an element the step then fills or types, so it gets
    the step's remaining time, never Playwright's own 30s default: without a
    timeout, a selector that never matches waited that default before the
    step's own ``timeout_ms`` even started. At least 1ms because Playwright
    reads ``timeout=0`` as "no timeout". A probe that runs out answers
    ``None``, which redacts -- the safe direction -- and the step's own action
    then fails with Playwright's error naming what it waited for.
    """
    return max(1, int((deadline - time.monotonic()) * 1000))


async def recorded_input_value(session: Any, value: str, probe: Callable[[], Awaitable[bool | None]]) -> str:
    """``REDACTED_INPUT_PLACEHOLDER`` if the redaction policy scrubs *value*, else *value*.

    *probe* classifies the target: ``True`` a credential, ``False`` not one,
    ``None`` could not tell. It is not run under ``off``. The page action always
    receives the original value -- only the JSONL row sees this result.
    """
    mode = resolve_redaction_mode()
    if mode == "off":
        return value
    verdict = await probe()
    if verdict is True:
        # Hiding it in the fill row alone is not enough: the page may echo
        # it into the console, a request, a socket frame or its own text,
        # and each of those rows is durable too. Admitted to the session
        # ledger before the page receives it, so the first echo is already
        # scrubbed. Only a field POSITIVELY classified as a credential is
        # admitted -- in ``all`` mode too, and not on a failed probe, which
        # still redacts the row. ``all`` hides every typed value from the
        # fill row, but blind-scrubbing a search term or a quantity from
        # every later row would corrupt the recording, not protect it.
        # Local import: the macros package imports the session stack.
        from octowright.macros.privacy import admit_redacted_input

        admit_redacted_input(session, value)
    # An unclassifiable field is treated as a credential -- the safe
    # direction to be wrong in.
    if mode == "all" or verdict is not False:
        return REDACTED_INPUT_PLACEHOLDER
    return value


def live_scrubbed(session: Any, value: Any) -> Any:
    """*value* scrubbed of the session's privacy ledger, for an IN-MEMORY buffer.

    The ledger was applied only at the recorder boundary, so the console ring,
    the page-error list and the network deque kept a page echo of a typed
    password in cleartext -- and ``browser_console_messages``,
    ``browser_network_requests``, their summaries and the dashboard's live
    ``/console`` hand those buffers straight back. Scrubbing at ingestion gives
    every reader one scrubbed copy, including the macro failure payload, which
    reads the same buffers and scrubs again anyway. What it cannot do is reach
    back: an entry buffered before a value was admitted keeps it, but such an
    entry predates the value being typed.

    ``active is True`` rather than truthiness: a mock session answers every
    attribute read with something truthy.
    """
    scrub = getattr(session, "durable_text_scrubber", None)
    if scrub is None or getattr(scrub, "active", False) is not True:
        return value
    return scrub.scrub_value(value)
