# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_lint``'s ``parameter_specs`` findings, compared whole.

A spec naming no parameter does nothing, and a save that makes a parameter less
sensitive than the saved version shows its values from then on; both messages
are what the author reads before saving.
"""

from __future__ import annotations

from typing import Any

from octowright.macros.lint_specs import lint_parameter_specs, lint_sensitivity_shrink


def test_a_spec_naming_a_non_parameter_is_reported() -> None:
    macro: dict[str, Any] = {"parameters": ["a"], "parameter_specs": {"b": {"sensitive": True}}}
    assert lint_parameter_specs(macro) == [
        (
            "unknown_parameter_spec",
            "parameter_specs names 'b', which is not one of the macro's parameters; "
            "only a top-level parameter can be declared",
        )
    ]


def test_parameters_written_as_an_object_are_known_by_their_keys() -> None:
    macro = {"parameters": {"display": {"type": "string"}}, "parameter_specs": {"display": {"sensitive": True}}}
    assert lint_parameter_specs(macro) == []


def test_dropping_a_declaration_is_reported_as_a_shrink() -> None:
    previous = {
        "parameters": ["page"],
        "parameter_specs": {"display": {"sensitive": True}, "nickname": {"sensitive": True}},
    }
    current = {"parameters": ["page", "display", "nickname"]}
    assert lint_sensitivity_shrink(current, previous) == [
        (
            "sensitive_parameters_shrank",
            "parameter(s) 'display', 'nickname' were sensitive in the saved version and are not in this one, "
            "so their values will be shown in run results and left out of the scrub set; declare them "
            '{"sensitive": true} in parameter_specs if that is not intended',
        )
    ]
