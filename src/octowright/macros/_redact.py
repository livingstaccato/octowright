# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared credential-redaction helper for macro action dicts.

Split out of ``execution.py`` so ``repair.py`` can redact the same way
without creating a circular import (``execution.py`` already imports
``suggest_fix``/``repair_preview``/``repair_apply`` from ``repair.py``).
"""

from __future__ import annotations

from typing import Any

# Action kinds whose ``value`` field carries user-supplied data that often
# resolves to a credential (``{{password}}``-style placeholders are resolved
# in-place by ``substitute()`` before dispatch). Redacted before the action
# dict is embedded in a RuntimeError payload, a repair-tool response, the
# macro-pill, or any log line.
_REDACTED_MACRO_VALUE = "<redacted>"
_REDACT_VALUE_ACTIONS: frozenset[str] = frozenset({"fill", "type", "fill_by"})


def _redact_action(action: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``action`` with credential-bearing fields
    replaced by ``<redacted>``. Non-redacted actions return a copy unchanged
    so callers can mutate freely without aliasing back into the macro list."""
    redacted = dict(action)
    if redacted.get("action") in _REDACT_VALUE_ACTIONS:
        for key in ("value", "text"):
            if key in redacted:
                redacted[key] = _REDACTED_MACRO_VALUE
    return redacted
