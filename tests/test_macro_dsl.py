# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.macros.dsl import compile_macro_document, compile_macro_yaml, parse_macro_yaml


def test_parse_macro_yaml_empty() -> None:
    assert parse_macro_yaml("") == {}


def test_compile_shorthand_examples() -> None:
    text = """
name: shorthand
description: basic shorthand
parameters:
  - user
actions:
  - navigate: "https://octowright.com/login"
  - click: "#next"
  - fill:
      selector: "#user"
      value: "{{user}}"
  - press_key: Escape
"""
    compiled = compile_macro_yaml(text, name="override", strict=True)
    assert compiled["name"] == "override"
    assert compiled["description"] == "basic shorthand"
    assert compiled["parameters"] == ["user"]
    assert compiled["actions"] == [
        {"action": "navigate", "url": "https://octowright.com/login"},
        {"action": "click", "selector": "#next"},
        {"action": "fill", "selector": "#user", "value": "{{user}}"},
        {"action": "press_key", "key": "Escape"},
    ]


def test_compile_nested_conditionals() -> None:
    text = """
name: nested
actions:
  - if_selector:
      selector: ".banner"
      then:
        - click: ".close"
        - try:
            actions:
              - fill:
                  selector: "#q"
                  value: "x"
      else:
        - try_each:
            branches:
              - [press_key: Escape]
              - [{action: click, selector: ".dismiss"}]
"""
    compiled = compile_macro_yaml(text, strict=True)
    if_selector = compiled["actions"][0]
    assert if_selector["action"] == "if_selector"
    assert len(if_selector["then"]) == 2
    assert if_selector["then"][1]["action"] == "try"
    assert if_selector["then"][1]["actions"][0]["action"] == "fill"
    assert len(if_selector["else"]) == 1
    assert if_selector["else"][0]["action"] == "try_each"
    assert if_selector["else"][0]["branches"][0][0]["action"] == "press_key"
    assert if_selector["else"][0]["branches"][1][0]["selector"] == ".dismiss"


def test_compile_explicit_actions_pass_through_recursively() -> None:
    text = """
name: explicit
actions:
  - action: if_selector
    selector: ".needs-check"
    then:
      - action: click
        selector: "#agree"
  - action: try
    actions:
      - action: fill
        selector: "#q"
        value: "manual"
"""
    compiled = compile_macro_yaml(text, strict=True)
    assert compiled["actions"] == [
        {
            "action": "if_selector",
            "selector": ".needs-check",
            "then": [{"action": "click", "selector": "#agree"}],
        },
        {
            "action": "try",
            "actions": [{"action": "fill", "selector": "#q", "value": "manual"}],
        },
    ]


def test_compile_strict_errors_on_missing_fields_and_malformed_shapes() -> None:
    cases = [
        ("missing_actions", "name: bad\n", "macro is missing required field 'actions'"),
        ("non_dict_action", "name: bad\nactions:\n  - not-an-object\n", "action must be an object"),
        ("malformed_try_each_branch", "name: bad\nactions:\n  - try_each:\n      branches: [ 1 ]\n", "must be a list"),
        (
            "navigate_missing_url",
            "name: bad\nactions:\n  - {navigate: null}\n",
            "navigate is missing required field 'url'",
        ),
        (
            "click_missing_selector",
            "name: bad\nactions:\n  - click: null\n",
            "click is missing required field 'selector'",
        ),
        (
            "fill_missing_value",
            "name: bad\nactions:\n  - fill:\n      selector: '#x'\n",
            "fill is missing required field 'value'",
        ),
    ]
    for _label, text, message in cases:
        with pytest.raises(ValueError, match=message):
            compile_macro_yaml(text, strict=True)


def test_compile_non_strict_returns_best_effort() -> None:
    text = """
name: tolerant
actions:
  - click: "#ok"
  - 123
  - try_each:
      branches:
        - [click: "#branch"]
        - bad-branch
"""
    compiled = compile_macro_yaml(text, strict=False)
    assert compiled["name"] == "tolerant"
    assert len(compiled["actions"]) == 2
    assert compiled["actions"][0] == {"action": "click", "selector": "#ok"}
    assert compiled["actions"][1]["action"] == "try_each"
    assert compiled["actions"][1]["branches"] == [[{"action": "click", "selector": "#branch"}]]


def test_compile_document_function_with_custom_name() -> None:
    doc = {
        "description": "from-doc",
        "actions": [{"click": "#x"}],
    }
    compiled = compile_macro_document(doc, name="from-function")
    assert compiled["name"] == "from-function"
    assert compiled["description"] == "from-doc"
