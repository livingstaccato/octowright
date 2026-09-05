# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for `octowright.macros.dsl`.

Each test pins a specific compilation behavior so that mutations to the
branch / message / default value would break at least one assertion.
"""

from __future__ import annotations

import pytest

from octowright.macros.dsl import compile_macro_document, compile_macro_yaml, parse_macro_yaml

# --- parse_macro_yaml --------------------------------------------------------


class TestParseMacroYaml:
    def test_empty_string_returns_empty_dict(self) -> None:
        """Empty input → {} (not None, not [])."""
        assert parse_macro_yaml("") == {}

    def test_whitespace_only_returns_empty_dict(self) -> None:
        """Whitespace-only YAML loads to None → {}."""
        assert parse_macro_yaml("   \n\n  ") == {}

    def test_yaml_null_returns_empty_dict(self) -> None:
        """Explicit `null` document → {}."""
        assert parse_macro_yaml("null") == {}

    def test_dict_passes_through(self) -> None:
        """Dict YAML returns the parsed mapping unchanged."""
        assert parse_macro_yaml("name: x\nactions: []\n") == {"name": "x", "actions": []}

    def test_list_root_raises_with_type_in_message(self) -> None:
        """Non-dict root raises with type name in the message."""
        with pytest.raises(ValueError, match="must be a mapping, got list"):
            parse_macro_yaml("- a\n- b\n")

    def test_string_root_raises_with_type_in_message(self) -> None:
        """String root mentions 'str' specifically."""
        with pytest.raises(ValueError, match="must be a mapping, got str"):
            parse_macro_yaml("plain-string")

    def test_int_root_raises_with_type_in_message(self) -> None:
        """Int root mentions 'int' specifically."""
        with pytest.raises(ValueError, match="must be a mapping, got int"):
            parse_macro_yaml("42")


# --- compile_macro_document: top-level shape --------------------------------


class TestCompileDocumentShape:
    def test_minimal_doc_returns_four_keys(self) -> None:
        """Compiled output has exactly the 4 documented keys (no extras)."""
        compiled = compile_macro_document({"actions": []})
        assert set(compiled.keys()) == {"name", "description", "parameters", "actions"}

    def test_default_name_is_macro_string(self) -> None:
        """When no name in doc and no override, default is the literal 'macro'."""
        compiled = compile_macro_document({"actions": []})
        assert compiled["name"] == "macro"

    def test_doc_name_used_when_no_override(self) -> None:
        """name field from doc is used."""
        compiled = compile_macro_document({"name": "from-doc", "actions": []})
        assert compiled["name"] == "from-doc"

    def test_override_name_wins_over_doc(self) -> None:
        """name= argument overrides doc['name']."""
        compiled = compile_macro_document({"name": "from-doc", "actions": []}, name="from-arg")
        assert compiled["name"] == "from-arg"

    def test_name_is_stringified(self) -> None:
        """Non-string name gets coerced to str()."""
        compiled = compile_macro_document({"name": 42, "actions": []})
        assert compiled["name"] == "42"

    def test_description_default_is_none(self) -> None:
        """Missing description → None (not empty string)."""
        compiled = compile_macro_document({"actions": []})
        assert compiled["description"] is None

    def test_description_passes_through(self) -> None:
        """Description preserved as-is."""
        compiled = compile_macro_document({"description": "hi", "actions": []})
        assert compiled["description"] == "hi"

    def test_parameters_default_is_empty_list(self) -> None:
        """Missing parameters → []."""
        compiled = compile_macro_document({"actions": []})
        assert compiled["parameters"] == []

    def test_actions_default_is_empty_list_in_non_strict(self) -> None:
        """Missing actions in non-strict → []."""
        compiled = compile_macro_document({}, strict=False)
        assert compiled["actions"] == []

    def test_strict_missing_actions_raises(self) -> None:
        """Strict mode raises when actions is missing entirely."""
        with pytest.raises(ValueError, match="macro is missing required field 'actions'"):
            compile_macro_document({}, strict=True)

    def test_strict_actions_not_list_raises(self) -> None:
        """Strict mode raises when actions is not a list."""
        with pytest.raises(ValueError, match="macro is missing required field 'actions'"):
            compile_macro_document({"actions": "not-a-list"}, strict=True)

    def test_strict_non_dict_doc_raises_with_type(self) -> None:
        """Strict raises with type name when doc is not a mapping."""
        with pytest.raises(ValueError, match="macro document must be a mapping, got list"):
            compile_macro_document([1, 2, 3], strict=True)

    def test_non_strict_non_dict_doc_returns_defaults(self) -> None:
        """Non-strict: non-dict doc returns the defaults shape."""
        compiled = compile_macro_document(["junk"], strict=False)
        assert compiled == {"name": "macro", "description": None, "parameters": [], "actions": []}


# --- _coerce_parameters ------------------------------------------------------


class TestCoerceParameters:
    def test_none_returns_empty(self) -> None:
        """None parameters → []."""
        assert compile_macro_document({"actions": [], "parameters": None})["parameters"] == []

    def test_list_of_strings(self) -> None:
        """list[str] passed through verbatim, in order."""
        compiled = compile_macro_document({"actions": [], "parameters": ["a", "b", "c"]})
        assert compiled["parameters"] == ["a", "b", "c"]

    def test_strict_non_list_raises_with_type(self) -> None:
        """Strict mode: non-list parameters raises with type."""
        with pytest.raises(ValueError, match="must be a list, got str"):
            compile_macro_document({"actions": [], "parameters": "not-a-list"}, strict=True)

    def test_non_strict_non_list_returns_empty(self) -> None:
        """Non-strict: non-list parameters becomes []."""
        compiled = compile_macro_document({"actions": [], "parameters": "junk"}, strict=False)
        assert compiled["parameters"] == []

    def test_strict_non_string_member_raises_with_repr(self) -> None:
        """Strict: non-str parameter raises with repr."""
        with pytest.raises(ValueError, match=r"macro parameter 42 must be a string"):
            compile_macro_document({"actions": [], "parameters": ["a", 42]}, strict=True)

    def test_non_strict_non_string_member_str_coerced(self) -> None:
        """Non-strict: non-str members get str()-coerced."""
        compiled = compile_macro_document({"actions": [], "parameters": ["a", 42, None]}, strict=False)
        assert compiled["parameters"] == ["a", "42", "None"]


# --- shorthand expansion -----------------------------------------------------


class TestScalarShorthand:
    def test_navigate_scalar(self) -> None:
        """`{navigate: url}` → `{action: navigate, url: ...}`."""
        compiled = compile_macro_yaml("name: x\nactions:\n  - navigate: 'https://x.com'\n")
        assert compiled["actions"] == [{"action": "navigate", "url": "https://x.com"}]

    def test_click_scalar(self) -> None:
        """`{click: selector}` → `{action: click, selector: ...}`."""
        compiled = compile_macro_yaml("name: x\nactions:\n  - click: '#btn'\n")
        assert compiled["actions"] == [{"action": "click", "selector": "#btn"}]

    def test_press_key_scalar(self) -> None:
        """`{press_key: key}` → `{action: press_key, key: ...}`."""
        compiled = compile_macro_yaml("name: x\nactions:\n  - press_key: Enter\n")
        assert compiled["actions"] == [{"action": "press_key", "key": "Enter"}]


class TestMappingShorthand:
    def test_fill_mapping(self) -> None:
        """`{fill: {selector,value}}` → `{action: fill, ...}`."""
        compiled = compile_macro_yaml("name: x\nactions:\n  - fill:\n      selector: '#u'\n      value: ziggy\n")
        assert compiled["actions"] == [{"action": "fill", "selector": "#u", "value": "ziggy"}]

    def test_if_selector_mapping(self) -> None:
        """`{if_selector: {selector, then, else}}` expands."""
        compiled = compile_macro_yaml(
            "name: x\nactions:\n  - if_selector:\n      selector: '.x'\n      then:\n        - click: '#a'\n"
        )
        action = compiled["actions"][0]
        assert action["action"] == "if_selector"
        assert action["selector"] == ".x"

    def test_try_mapping(self) -> None:
        """`{try: {actions: [...]}}` expands."""
        compiled = compile_macro_yaml("name: x\nactions:\n  - try:\n      actions:\n        - click: '#a'\n")
        assert compiled["actions"][0]["action"] == "try"

    def test_try_each_mapping(self) -> None:
        """`{try_each: {branches: [...]}}` expands."""
        compiled = compile_macro_yaml("name: x\nactions:\n  - try_each:\n      branches:\n        - [click: '#a']\n")
        assert compiled["actions"][0]["action"] == "try_each"

    def test_strict_fill_non_dict_raises_with_specific_message(self) -> None:
        """fill shorthand with non-dict payload has its own error message."""
        with pytest.raises(ValueError, match="fill shorthand requires a mapping payload"):
            compile_macro_yaml("name: x\nactions:\n  - fill: 'just-a-string'\n", strict=True)

    def test_strict_if_selector_non_dict_uses_generic_message(self) -> None:
        """Non-fill mapping shorthand with non-dict uses generic error."""
        with pytest.raises(ValueError, match="if_selector shorthand must be a mapping, got str"):
            compile_macro_yaml("name: x\nactions:\n  - if_selector: 'oops'\n", strict=True)


class TestNormalizeShorthandEdgeCases:
    def test_explicit_action_field_skips_shorthand(self) -> None:
        """When 'action' is already present, normalization is skipped."""
        text = "name: x\nactions:\n  - {action: click, selector: '#a'}\n"
        compiled = compile_macro_yaml(text)
        assert compiled["actions"] == [{"action": "click", "selector": "#a"}]

    def test_unknown_single_key_passes_through(self) -> None:
        """Single-key dict whose key is NOT a known action returns the node as-is."""
        # In strict mode, it raises because there's no 'action' field.
        with pytest.raises(ValueError, match="missing or invalid 'action' field"):
            compile_macro_yaml("name: x\nactions:\n  - unknown_thing: payload\n", strict=True)

    def test_multi_key_dict_skips_shorthand(self) -> None:
        """A 2-key dict without 'action' field falls through (errors in strict)."""
        with pytest.raises(ValueError, match="missing or invalid 'action' field"):
            compile_macro_yaml("name: x\nactions:\n  - {foo: 1, bar: 2}\n", strict=True)


# --- _compile_if_selector ----------------------------------------------------


class TestCompileIfSelector:
    def test_then_only_branch(self) -> None:
        """`then` populated, no `else` field at all."""
        text = "name: x\nactions:\n  - action: if_selector\n    selector: '.x'\n    then:\n      - click: '#a'\n"
        compiled = compile_macro_yaml(text)
        action = compiled["actions"][0]
        assert action["then"] == [{"action": "click", "selector": "#a"}]
        assert "else" not in action

    def test_else_only_branch(self) -> None:
        """`else` populated, no `then` field."""
        text = "name: x\nactions:\n  - action: if_selector\n    selector: '.x'\n    else:\n      - click: '#b'\n"
        compiled = compile_macro_yaml(text)
        action = compiled["actions"][0]
        assert action["else"] == [{"action": "click", "selector": "#b"}]
        assert "then" not in action

    def test_both_branches(self) -> None:
        """Both then and else populated."""
        text = (
            "name: x\nactions:\n  - action: if_selector\n    selector: '.x'\n"
            "    then:\n      - click: '#t'\n    else:\n      - click: '#e'\n"
        )
        compiled = compile_macro_yaml(text)
        action = compiled["actions"][0]
        assert action["then"][0]["selector"] == "#t"
        assert action["else"][0]["selector"] == "#e"

    def test_neither_branch_strict_passes(self) -> None:
        """if_selector with no branches is permitted (no required-fields validation)."""
        text = "name: x\nactions:\n  - action: if_selector\n    selector: '.x'\n"
        compiled = compile_macro_yaml(text)
        assert compiled["actions"][0] == {"action": "if_selector", "selector": ".x"}

    def test_strict_missing_selector_raises(self) -> None:
        """Strict raises when 'selector' missing."""
        text = "name: x\nactions:\n  - action: if_selector\n    then:\n      - click: '#a'\n"
        with pytest.raises(ValueError, match="if_selector is missing required field 'selector'"):
            compile_macro_yaml(text, strict=True)

    def test_strict_then_not_list_raises(self) -> None:
        """Strict raises when 'then' is not a list."""
        text = "name: x\nactions:\n  - action: if_selector\n    selector: '.x'\n    then: 'not-a-list'\n"
        with pytest.raises(ValueError, match=r"if_selector\.then must be a list, got str"):
            compile_macro_yaml(text, strict=True)

    def test_strict_else_not_list_raises(self) -> None:
        """Strict raises when 'else' is not a list."""
        text = "name: x\nactions:\n  - action: if_selector\n    selector: '.x'\n    else: 42\n"
        with pytest.raises(ValueError, match=r"if_selector\.else must be a list, got int"):
            compile_macro_yaml(text, strict=True)

    def test_non_strict_then_not_list_becomes_empty(self) -> None:
        """Non-strict: bad `then` becomes []."""
        text = "name: x\nactions:\n  - action: if_selector\n    selector: '.x'\n    then: 'oops'\n"
        compiled = compile_macro_yaml(text, strict=False)
        assert compiled["actions"][0]["then"] == []


# --- _compile_try ------------------------------------------------------------


class TestCompileTry:
    def test_empty_actions(self) -> None:
        """try with empty actions list compiles to empty list."""
        text = "name: x\nactions:\n  - action: try\n    actions: []\n"
        compiled = compile_macro_yaml(text)
        assert compiled["actions"][0]["actions"] == []

    def test_one_action(self) -> None:
        """try with one action compiles correctly."""
        text = "name: x\nactions:\n  - action: try\n    actions:\n      - click: '#a'\n"
        compiled = compile_macro_yaml(text)
        assert compiled["actions"][0]["actions"] == [{"action": "click", "selector": "#a"}]

    def test_many_actions(self) -> None:
        """try with multiple actions preserves order."""
        text = (
            "name: x\nactions:\n  - action: try\n    actions:\n"
            "      - click: '#a'\n      - click: '#b'\n      - click: '#c'\n"
        )
        compiled = compile_macro_yaml(text)
        actions = compiled["actions"][0]["actions"]
        assert [a["selector"] for a in actions] == ["#a", "#b", "#c"]

    def test_strict_missing_actions_raises(self) -> None:
        """Strict: missing 'actions' field raises with the documented message."""
        text = "name: x\nactions:\n  - action: try\n"
        with pytest.raises(ValueError, match="try is missing required field 'actions' list"):
            compile_macro_yaml(text, strict=True)

    def test_strict_non_list_actions_raises(self) -> None:
        """Strict: non-list 'actions' raises."""
        text = "name: x\nactions:\n  - action: try\n    actions: 'not-a-list'\n"
        with pytest.raises(ValueError, match="try is missing required field 'actions' list"):
            compile_macro_yaml(text, strict=True)

    def test_non_strict_missing_actions_returns_empty(self) -> None:
        """Non-strict: missing 'actions' becomes []."""
        text = "name: x\nactions:\n  - action: try\n"
        compiled = compile_macro_yaml(text, strict=False)
        assert compiled["actions"][0]["actions"] == []


# --- _compile_try_each -------------------------------------------------------


class TestCompileTryEach:
    def test_empty_branches(self) -> None:
        """try_each with no branches compiles to empty list."""
        text = "name: x\nactions:\n  - action: try_each\n    branches: []\n"
        compiled = compile_macro_yaml(text)
        assert compiled["actions"][0]["branches"] == []

    def test_one_branch(self) -> None:
        """try_each with one branch."""
        text = "name: x\nactions:\n  - action: try_each\n    branches:\n      - [click: '#a']\n"
        compiled = compile_macro_yaml(text)
        assert compiled["actions"][0]["branches"] == [[{"action": "click", "selector": "#a"}]]

    def test_many_branches(self) -> None:
        """try_each branch order preserved."""
        text = (
            "name: x\nactions:\n  - action: try_each\n    branches:\n"
            "      - [click: '#a']\n      - [click: '#b']\n      - [click: '#c']\n"
        )
        compiled = compile_macro_yaml(text)
        branches = compiled["actions"][0]["branches"]
        assert [b[0]["selector"] for b in branches] == ["#a", "#b", "#c"]

    def test_strict_missing_branches_raises(self) -> None:
        """Strict: missing 'branches' raises."""
        text = "name: x\nactions:\n  - action: try_each\n"
        with pytest.raises(ValueError, match="try_each is missing required field 'branches' list"):
            compile_macro_yaml(text, strict=True)

    def test_strict_non_list_branches_raises(self) -> None:
        """Strict: non-list 'branches' raises with same message as missing."""
        text = "name: x\nactions:\n  - action: try_each\n    branches: 'oops'\n"
        with pytest.raises(ValueError, match="try_each is missing required field 'branches' list"):
            compile_macro_yaml(text, strict=True)

    def test_strict_non_list_branch_member_raises_with_index(self) -> None:
        """Strict: a single non-list entry in branches raises with index in path."""
        text = "name: x\nactions:\n  - action: try_each\n    branches: [ 1 ]\n"
        with pytest.raises(ValueError, match=r"branches\[0\] must be a list, got int"):
            compile_macro_yaml(text, strict=True)

    def test_non_strict_non_list_branch_skipped(self) -> None:
        """Non-strict: non-list branch entries are dropped, valid ones kept."""
        text = (
            "name: x\nactions:\n  - action: try_each\n    branches:\n"
            "      - [click: '#a']\n      - bad-branch\n      - [click: '#b']\n"
        )
        compiled = compile_macro_yaml(text, strict=False)
        # Two valid branches survive, the bad-branch is dropped.
        branches = compiled["actions"][0]["branches"]
        assert len(branches) == 2
        assert [b[0]["selector"] for b in branches] == ["#a", "#b"]


# --- _validate_required_simple ----------------------------------------------


class TestValidateRequiredSimple:
    def test_navigate_empty_string_url_raises(self) -> None:
        """Empty-string url is treated as missing in strict mode."""
        text = "name: x\nactions:\n  - action: navigate\n    url: ''\n"
        with pytest.raises(ValueError, match="navigate is missing required field 'url'"):
            compile_macro_yaml(text, strict=True)

    def test_click_empty_selector_raises(self) -> None:
        """Empty-string selector → missing-field error."""
        text = "name: x\nactions:\n  - action: click\n    selector: ''\n"
        with pytest.raises(ValueError, match="click is missing required field 'selector'"):
            compile_macro_yaml(text, strict=True)

    def test_fill_missing_value_raises(self) -> None:
        """fill with selector but no value → missing-field error."""
        text = "name: x\nactions:\n  - action: fill\n    selector: '#u'\n"
        with pytest.raises(ValueError, match="fill is missing required field 'value'"):
            compile_macro_yaml(text, strict=True)

    def test_fill_missing_selector_raises(self) -> None:
        """fill with value but no selector → missing-field error."""
        text = "name: x\nactions:\n  - action: fill\n    value: 'ziggy'\n"
        with pytest.raises(ValueError, match="fill is missing required field 'selector'"):
            compile_macro_yaml(text, strict=True)


# --- _compile_action ---------------------------------------------------------


class TestCompileAction:
    def test_unknown_action_passes_through(self) -> None:
        """Unknown 'action' kind isn't validated, just passed through."""
        text = "name: x\nactions:\n  - action: my_custom_thing\n    foo: 1\n"
        compiled = compile_macro_yaml(text)
        assert compiled["actions"][0] == {"action": "my_custom_thing", "foo": 1}

    def test_strict_non_dict_action_raises_with_type(self) -> None:
        """Non-dict action raises with type name in message."""
        with pytest.raises(ValueError, match=r"action must be an object, got int"):
            compile_macro_yaml("name: x\nactions:\n  - 42\n", strict=True)

    def test_strict_non_dict_action_string_message(self) -> None:
        """Non-dict action with string type — must mention 'str'."""
        with pytest.raises(ValueError, match=r"action must be an object, got str"):
            compile_macro_yaml("name: x\nactions:\n  - 'plain string'\n", strict=True)

    def test_non_strict_non_dict_action_dropped(self) -> None:
        """Non-strict: non-dict actions are dropped from output."""
        text = "name: x\nactions:\n  - 42\n  - click: '#a'\n  - 'junk'\n"
        compiled = compile_macro_yaml(text, strict=False)
        assert compiled["actions"] == [{"action": "click", "selector": "#a"}]

    def test_path_includes_index(self) -> None:
        """Error path includes the actions[N] index of the failing entry."""
        text = "name: x\nactions:\n  - click: '#ok'\n  - 42\n"
        with pytest.raises(ValueError, match=r"actions\[1\]: action must be an object"):
            compile_macro_yaml(text, strict=True)


# --- compile_macro_yaml integration -----------------------------------------


class TestCompileMacroYamlIntegration:
    def test_strict_default_is_true(self) -> None:
        """compile_macro_yaml defaults to strict=True."""
        with pytest.raises(ValueError):
            compile_macro_yaml("not-a-mapping")

    def test_name_override_through_yaml_path(self) -> None:
        """name= argument flows through compile_macro_yaml."""
        compiled = compile_macro_yaml("actions: []\n", name="explicit")
        assert compiled["name"] == "explicit"

    def test_nested_path_in_error(self) -> None:
        """Nested error path includes the full breadcrumb."""
        text = "name: x\nactions:\n  - action: if_selector\n    selector: '.x'\n    then:\n      - 99\n"
        with pytest.raises(ValueError, match=r"actions\[0\].then\[0\]: action must be an object"):
            compile_macro_yaml(text, strict=True)


def test_compile_macro_yaml_strict_by_default() -> None:
    import pytest

    from octowright.macros.dsl import compile_macro_yaml

    with pytest.raises(ValueError):
        compile_macro_yaml("actions:\\n  - not_an_action")
