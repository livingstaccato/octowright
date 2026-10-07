# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What the nested-action walk yields when it follows a ``macro_call``."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from octowright.macros.nesting import iter_nested_actions


def _loader(macros: dict[str, list[dict[str, Any]]]) -> Any:
    def load(name: str) -> dict[str, Any]:
        return {"name": name, "actions": macros[name]}

    return load


def test_a_placeholder_in_a_called_action_kind_is_expanded_from_the_call_args() -> None:
    call = {"action": "macro_call", "name": "child", "args": {"kind": "expect_network_clean"}}

    walked = list(iter_nested_actions([call], load_macro=_loader({"child": [{"action": "{{kind}}"}]})))

    assert walked == [call, {"action": "expect_network_clean"}]


def test_a_placeholder_in_a_nested_call_name_is_expanded_too() -> None:
    call = {"action": "macro_call", "name": "child", "args": {"target": "grandchild"}}
    macros = {
        "child": [{"action": "macro_call", "name": "{{target}}"}],
        "grandchild": [{"action": "expect_network_clean"}],
    }

    walked = list(iter_nested_actions([call], load_macro=_loader(macros)))

    assert walked == [
        call,
        {"action": "macro_call", "name": "grandchild"},
        {"action": "expect_network_clean"},
    ]


def test_call_args_that_are_not_a_dict_are_not_used_for_expansion() -> None:
    # Only a real dict is an args object; anything else expands against no args,
    # which leaves the placeholder unresolved and the called macro unwalked.
    call = {"action": "macro_call", "name": "child", "args": MappingProxyType({"kind": "click"})}

    walked = list(iter_nested_actions([call], load_macro=_loader({"child": [{"action": "{{kind}}"}]})))

    assert walked == [call]
