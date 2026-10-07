# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The macro DSL compiler: strict by default, and its errors name the path.

``macro_compile`` reports these messages to the author, and the path in each
one (``actions[0].then[1]``) is how they find the step at fault in a nested
document, so they are compared exactly. Non-strict compilation is what a
lenient preview uses; it must still produce the runtime's shape (a list where
a list is expected) rather than the malformed value it was given.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from octowright.macros.dsl import compile_macro_document, compile_macro_yaml


def _raises(doc: Any, message: str, **kwargs: Any) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        compile_macro_document(doc, **kwargs)


def test_strict_is_the_default() -> None:
    _raises([], "macro document must be a mapping, got list")
    _raises({"name": "m"}, "macro is missing required field 'actions'")
    _raises({"actions": [], "parameters": "x"}, "macro 'parameters' must be a list, got str")
    with pytest.raises(ValueError, match=r"^macro is missing required field 'actions'$"):
        compile_macro_yaml("name: m\n")


@pytest.mark.parametrize(
    ("actions", "message"),
    [
        ([{"action": "navigate"}], "actions[0]: navigate is missing required field 'url'"),
        ([{"fill": "x"}], "actions[0]: fill shorthand requires a mapping payload"),
        ([{"try": 3}], "actions[0]: try shorthand must be a mapping, got int"),
        (
            [{"action": "if_selector", "selector": "#a", "then": [], "else": [{"action": "click"}]}],
            "actions[0].else[0]: click is missing required field 'selector'",
        ),
        (
            [{"action": "if_selector", "selector": "#a", "then": [{"action": "click", "selector": "#b"}, 5]}],
            "actions[0].then[1]: action must be an object, got int",
        ),
        (
            [{"action": "try", "actions": [{"action": "fill", "selector": "#a"}]}],
            "actions[0].actions[0]: fill is missing required field 'value'",
        ),
        ([{"action": "try"}], "actions[0]: try is missing required field 'actions' list"),
        ([{"action": "try_each"}], "actions[0]: try_each is missing required field 'branches' list"),
        ([{"action": "try_each", "branches": [[], "x"]}], "actions[0].branches[1] must be a list, got str"),
        (
            [{"action": "try_each", "branches": [[], [{"action": "navigate", "url": ""}]]}],
            "actions[0].branches[1][0]: navigate is missing required field 'url'",
        ),
        (
            [{"action": "click", "selector": "#a"}, {"navigate": None}],
            "actions[1]: navigate is missing required field 'url'",
        ),
    ],
)
def test_strict_errors_name_the_path(actions: list[Any], message: str) -> None:
    _raises({"actions": actions}, message)


def test_lenient_compilation_keeps_the_runtime_shape() -> None:
    doc = {
        "parameters": "x",
        "actions": [
            {"action": "if_selector", "selector": "#a", "then": "nope", "else": "nope"},
            {"action": "try"},
            {"action": "try_each"},
            {"action": "try_each", "branches": [[{"action": "navigate"}], "x"]},
            {"fill": "x"},
        ],
    }
    assert compile_macro_document(doc, strict=False) == {
        "name": "macro",
        "description": None,
        "parameters": [],
        "actions": [
            {"action": "if_selector", "selector": "#a", "then": [], "else": []},
            {"action": "try", "actions": []},
            {"action": "try_each", "branches": []},
            {"action": "try_each", "branches": [[{"action": "navigate"}]]},
            {"fill": "x"},
        ],
    }
    assert compile_macro_document("x", strict=False)["actions"] == []
    assert compile_macro_document({}, strict=False)["actions"] == []
