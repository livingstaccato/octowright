# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Console-level classification, pinned to what the engines actually emit.

The spellings here were measured, not assumed: a page calling console.log /
info / debug / warn / error / trace / assert was driven under chromium,
firefox and webkit (Playwright 1.62) and every engine reported the same seven
levels — notably ``warning`` for ``console.warn`` in all three, and ``assert``
as its own level rather than folded into ``error``.
"""

from __future__ import annotations

import pytest

from octowright.console_levels import (
    count_errors,
    count_warnings,
    is_diagnostic_console_message,
    is_error_console_message,
    is_warning_console_message,
)

# Exactly what the three engines return for each console method.
MEASURED_LEVELS = {
    "log": "log",
    "info": "info",
    "debug": "debug",
    "warn": "warning",
    "error": "error",
    "trace": "trace",
    "assert": "assert",
}


def _msg(level: str) -> dict[str, str]:
    return {"level": level, "text": "x"}


@pytest.mark.parametrize("level", ["error", "assert", "ERROR", "Assert"])
def test_error_severity_levels(level: str) -> None:
    """``console.assert`` fires only when a declared invariant FAILED, so it is
    error severity. Classifying it as its own thing meant the one line naming a
    broken invariant was neither counted nor claimed by the macro failure tail."""
    assert is_error_console_message(_msg(level))
    assert is_diagnostic_console_message(_msg(level))


@pytest.mark.parametrize("level", ["warning", "warn", "WARNING"])
def test_warning_severity_levels(level: str) -> None:
    """All three engines emit ``warning``; ``warn`` is a defensive alias kept
    because it predates the shared module, not a current Firefox behaviour."""
    assert is_warning_console_message(_msg(level))
    assert is_diagnostic_console_message(_msg(level))


@pytest.mark.parametrize("level", ["log", "info", "debug", "trace"])
def test_non_diagnostic_levels(level: str) -> None:
    assert not is_diagnostic_console_message(_msg(level))


def test_every_measured_engine_level_classifies() -> None:
    """Guard against a level the engines emit that nothing here accounts for."""
    diagnostic = {level for level in MEASURED_LEVELS.values() if is_diagnostic_console_message(_msg(level))}

    assert diagnostic == {"error", "assert", "warning"}


def test_counts_split_error_and_warning() -> None:
    messages = [_msg("error"), _msg("assert"), _msg("warning"), _msg("warn"), _msg("log")]

    assert count_errors(messages) == 2
    assert count_warnings(messages) == 2


@pytest.mark.parametrize("entry", ["plain string", None, 42, {"no": "level"}])
def test_malformed_entries_never_raise(entry: object) -> None:
    """These run while another failure is being reported; they must not add one."""
    assert not is_diagnostic_console_message(entry)
    assert not is_error_console_message(entry)
