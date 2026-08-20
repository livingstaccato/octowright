# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Canonical classification of browser console message levels.

Lives at the package root, like :mod:`octowright.dashboard_events`, so the
``server/`` and ``session/`` layers and package-root summarizers can all share
it without any of them reaching into another's private modules -- and so a
pure summarization module does not have to import the browser stack to ask
whether a level is an error.

Why it is shared at all: ``capture_summaries``, ``server.browser.inspect_console``
and ``session.core_ops_mixin`` each classified levels independently and had
already drifted. Firefox reports ``console.warn``'s level as ``warn`` where
Chromium says ``warning``, and casing is not guaranteed, so every check must
match case-insensitively across both spellings; a copy that missed ``warn``
silently dropped Firefox warnings.
"""

from __future__ import annotations

from typing import Any

ERROR_CONSOLE_LEVELS = frozenset({"error"})
WARNING_CONSOLE_LEVELS = frozenset({"warning", "warn"})
# Levels that explain a failure: an error, or the warning that preceded it.
DIAGNOSTIC_CONSOLE_LEVELS = ERROR_CONSOLE_LEVELS | WARNING_CONSOLE_LEVELS


def console_level(message: Any) -> str:
    """Normalized level of a console entry, or ``""`` if it has none.

    Tolerates a non-dict entry: callers run this while another failure is
    being reported, so it must never be the thing that raises.
    """
    if not isinstance(message, dict):
        return ""
    return str(message.get("level", "")).lower()


def is_error_console_message(message: Any) -> bool:
    return console_level(message) in ERROR_CONSOLE_LEVELS


def is_warning_console_message(message: Any) -> bool:
    return console_level(message) in WARNING_CONSOLE_LEVELS


def is_diagnostic_console_message(message: Any) -> bool:
    """Whether a console entry is one that explains a failure."""
    return console_level(message) in DIAGNOSTIC_CONSOLE_LEVELS


def count_errors(messages: list[dict[str, Any]]) -> int:
    return sum(1 for message in messages if is_error_console_message(message))


def count_warnings(messages: list[dict[str, Any]]) -> int:
    return sum(1 for message in messages if is_warning_console_message(message))
