# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``parameter_specs``: how a macro's own declarations resolve, and what lint says about them.

The warnings are what the author reads (in a run result and in ``macro_lint``),
so they are compared whole; each names the macro, the parameter and the floor
that held an unmark down, never a value.
"""

from __future__ import annotations

from octowright.macros.lint_specs import lint_parameter_specs
from octowright.macros.parameter_specs import macro_privacy, resolve_macro_privacy

R = "<redacted>"
PW = "pw"  # pragma: allowlist secret (synthetic fixture)


def test_each_floor_says_why_the_unmark_was_ignored() -> None:
    macro = {
        "name": "checkout",
        "parameters": ["password", "card", "banned", "username"],
        "parameter_specs": {
            "password": {"sensitive": False},
            "card": {"sensitive": False},
            "banned": {"sensitive": False},
            "username": {"sensitive": False},
        },
        "actions": [{"action": "expect_no_text", "text": "{{banned}}"}],
    }
    view = resolve_macro_privacy(macro, credential_args=frozenset({"card"}))
    assert view.ignored == (
        "macro 'checkout': parameter_specs marks 'banned' not sensitive, but it is the text an expect_no_text "
        "step checks for, so it stays credential-tier",
        "macro 'checkout': parameter_specs marks 'card' not sensitive, but its value was passed as a credential "
        '({"credential": ...}), so it stays credential-tier',
        "macro 'checkout': parameter_specs marks 'password' not sensitive, but its name reads as a credential, "
        "so it stays credential-tier",
    )
    assert view.warnings == view.ignored


def test_a_name_that_is_not_a_string_gets_no_prefix() -> None:
    view = resolve_macro_privacy({"name": 5, "parameter_specs": {"x": 1}})
    assert view.warnings == ("parameter_specs['x'] must be an object such as {\"sensitive\": true}; ignored",)


def test_an_empty_parameter_name_is_a_problem() -> None:
    assert lint_parameter_specs({"parameters": [""], "parameter_specs": {"": {"sensitive": True}}}) == [
        ("bad_parameter_specs", "parameter_specs has a key that is not a parameter name; it was ignored"),
    ]


def test_macro_privacy_honours_the_callers_credential_args() -> None:
    privacy = macro_privacy({"actions": []}, credential_args=frozenset({"display"}))
    assert privacy.redact({"display": "Ada", "page": "2"}) == {"display": R, "page": "2"}


def test_a_macro_that_is_not_a_mapping_resolves_by_name_alone() -> None:
    view = resolve_macro_privacy(["not", "a", "macro"])
    assert (view.credential_args, view.warnings, view.ignored) == (frozenset(), (), ())
    assert view.privacy.redact({"password": PW, "page": "2"}) == {"password": R, "page": "2"}
