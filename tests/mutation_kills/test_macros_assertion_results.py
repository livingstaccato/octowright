# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a run's observed checks report: the step they ran at, and scrubbed of the run's values."""

from __future__ import annotations

from octowright.macros.assertion_results import begin_collecting, end_collecting, observe


def test_a_check_observed_before_any_step_is_assigned_reports_step_zero() -> None:
    results, token = begin_collecting()
    try:
        observe("expect_network_clean", {}, {"in_flight": 0, "in_flight_untracked": 0})
    finally:
        end_collecting(token)

    assert results.fields(()) == {
        "assertions": [{"step": 0, "action": "expect_network_clean", "in_flight": 0, "in_flight_untracked": 0}]
    }


def test_a_value_in_the_selector_is_replaced_with_the_redaction_marker() -> None:
    results, token = begin_collecting()
    try:
        results.step = 2
        observe("expect_no_text", {"selector": "#acct-Zq9wv7"}, {"matched": 1})
    finally:
        end_collecting(token)

    assert results.fields(("Zq9wv7",)) == {
        "assertions": [{"step": 2, "action": "expect_no_text", "matched": 1, "selector": "#acct-<redacted>"}]
    }
