# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The pill hint names every informative field it knows, each under its own key."""

from __future__ import annotations

import pytest

from octowright.macros.descriptions import describe_action


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "Sign in"),
        ("text", "Continue"),
        ("role", "button"),
        ("selector", "#go"),
        ("url", "https://app.example.test/"),
        ("key", "Enter"),
        ("value", "typed"),
    ],
)
def test_each_hint_field_alone_is_described_under_its_own_key(field: str, value: str) -> None:
    assert describe_action({"action": "step", field: value}) == f"step {field}={value}"


def test_the_hint_fields_are_tried_in_priority_order() -> None:
    action = {"action": "press", "value": "v", "key": "Enter", "url": "u", "role": "r", "text": "t"}

    assert describe_action(action) == "press text=t"
    del action["text"]
    assert describe_action(action) == "press role=r"
    del action["role"]
    assert describe_action(action) == "press url=u"
    del action["url"]
    assert describe_action(action) == "press key=Enter"
