# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Property-based testing for Octowright IO and substitution logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from octowright import recorder
from octowright.macros import substitute


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.dictionaries(keys=st.text(min_size=1), values=st.text())), st.booleans())
def test_tail_log_robustness(tmp_path: Path, events: list[dict], ends_with_newline: bool):
    """Verify tail_log handles any JSON data and partial line fragments."""
    path = tmp_path / "hypo_tail.jsonl"

    # Construct raw data
    raw_text = "".join(json.dumps(e) + "\n" for e in events)
    if events and not ends_with_newline:
        raw_text = raw_text.rstrip("\n")

    path.write_text(raw_text, encoding="utf-8")

    # Tail from beginning
    results, next_cursor, _total_bytes = recorder.tail_log(path, 0)

    if ends_with_newline or not events:
        assert len(results) == len(events)
        assert next_cursor == path.stat().st_size
    else:
        # If it doesn't end with newline, the last line is a fragment and skipped
        assert len(results) == len(events) - 1


@given(
    st.lists(st.dictionaries(keys=st.text(min_size=1), values=st.text())),
    st.dictionaries(keys=st.text(min_size=1), values=st.text()),
)
def test_macro_substitution_robustness(actions: list[dict], args: dict):
    """Verify macro substitution handles any character data and missing keys."""
    # Add placeholders to some actions
    for i, action in enumerate(actions):
        if i % 2 == 0 and args:
            key = next(iter(args))
            action["placeholder"] = "{{" + key + "}}"

    try:
        result = substitute(actions, args)
        assert len(result) == len(actions)
    except KeyError:
        # Expected if a placeholder is inserted that isn't in args
        pass
    except Exception as e:
        pytest.fail(f"Substitution raised unexpected exception: {e}")
