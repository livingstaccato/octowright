# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A macro failure must carry the console line that explains it.

Field report: a payload said "timed out waiting for #student-name-edit"
while ``net::ERR_NETWORK_CHANGED`` sat unread in the session's console ring
buffer, so the cause was only findable by opening the raw JSONL afterward.
"""

from __future__ import annotations

from typing import Any

from octowright.session.core_ops_mixin import _select_console_tail


def _log(index: int) -> dict[str, Any]:
    return {"level": "log", "text": f"chatty {index}"}


NETWORK_ERROR = {"level": "error", "text": "Failed to load resource: net::ERR_NETWORK_CHANGED"}


def test_zero_limit_still_returns_nothing() -> None:
    """Back-compat: the default (0) must stay an empty list, not a tail."""
    assert _select_console_tail([_log(0), NETWORK_ERROR], 0) == []
    assert _select_console_tail([], 10) == []


def test_plain_tail_when_nothing_is_diagnostic() -> None:
    messages = [_log(index) for index in range(50)]
    assert _select_console_tail(messages, 5) == messages[-5:]


def test_error_survives_a_chatty_page() -> None:
    """The regression: a plain tail loses the one useful line."""
    messages = [NETWORK_ERROR, *(_log(index) for index in range(50))]

    selected = _select_console_tail(messages, 10)

    assert len(selected) == 10
    assert NETWORK_ERROR in selected
    assert messages[-10:] != selected  # a plain tail would have dropped it


def test_selection_stays_chronological() -> None:
    messages = [_log(0), NETWORK_ERROR, _log(1), _log(2)]

    assert _select_console_tail(messages, 4) == messages


def test_warnings_and_asserts_count_as_diagnostic() -> None:
    for level in ("error", "warning", "assert"):
        marker = {"level": level, "text": "explains the failure"}
        messages = [marker, *(_log(index) for index in range(20))]
        assert marker in _select_console_tail(messages, 3), level


def test_diagnostics_beyond_the_limit_keep_the_most_recent() -> None:
    errors = [{"level": "error", "text": f"boom {index}"} for index in range(5)]

    selected = _select_console_tail([*errors, _log(0)], 2)

    assert selected == errors[-2:]


def test_non_dict_entries_never_raise_into_the_failure_path() -> None:
    """This runs while another failure is being reported; it must not add one."""
    messages = ["plain string", None, NETWORK_ERROR, _log(0)]

    selected = _select_console_tail(messages, 3)

    assert NETWORK_ERROR in selected
    assert len(selected) == 3
