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

from octowright.macros.execution import (
    MACRO_FAILURE_CONSOLE_TEXT_CHARS,
    _truncate_bundle_console,
)
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


def test_all_engine_spellings_of_a_diagnostic_level_count() -> None:
    """Firefox says "warn" where Chromium says "warning", and casing is not
    guaranteed -- the shared predicate covers both, which a level set local
    to this module had already got wrong."""
    for level in ("error", "warning", "warn", "ERROR", "Warn"):
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


def test_a_huge_console_message_is_capped_before_it_reaches_the_client() -> None:
    """The count bound does not bound size: a page that logs a stringified API
    response or a base64 data URL would otherwise put multi-MB strings into the
    error payload and push them over the MCP transport."""
    bundle = _truncate_bundle_console(
        {"console_tail": [{"level": "error", "text": "x" * 50_000}, {"level": "log", "text": "short"}]}
    )

    capped, short = bundle["console_tail"]
    assert len(capped["text"]) < MACRO_FAILURE_CONSOLE_TEXT_CHARS + 32
    assert capped["text"].endswith("[truncated]")
    assert short["text"] == "short"


def test_truncation_tolerates_a_malformed_bundle() -> None:
    """Runs on the failure path; it must not become the failure."""
    assert _truncate_bundle_console({"url": "x"}) == {"url": "x"}
    assert _truncate_bundle_console({"console_tail": None}) == {"console_tail": None}
    assert _truncate_bundle_console({"console_tail": ["junk", {"level": "error"}]})["console_tail"][0] == "junk"


def test_the_selector_copies_so_no_consumer_can_corrupt_the_live_buffer() -> None:
    """``list(session.console)`` copies the LIST, not the dicts inside it, so
    returning the originals handed callers live ring-buffer entries -- and one
    consumer capping an entry's length silently rewrote the session's console
    history for every later reader. Fixed at the producer so the whole class of
    bug is gone, not one instance of it.
    """
    from collections import deque

    ring: deque[dict[str, Any]] = deque([{"level": "error", "text": "X" * 50_000}], maxlen=1000)
    selected = _select_console_tail(list(ring), 10)
    assert selected[0] is not ring[0], "the selector must hand back a copy"

    bundle = _truncate_bundle_console({"console_tail": selected})

    assert len(ring[0]["text"]) == 50_000, "the live console buffer must be untouched"
    assert bundle["console_tail"][0]["text"].endswith("[truncated]")
