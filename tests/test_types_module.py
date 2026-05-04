# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.types import ClickAction, LaunchOptions, MacroAction, NavigateAction


def test_launch_options_typed_dict_usage() -> None:
    opts: LaunchOptions = {
        "kind": "chromium",
        "url": "https://example.com",
        "headed": False,
        "har": True,
    }
    assert opts["kind"] == "chromium"
    assert opts["har"] is True


def test_macro_action_union_accepts_known_actions() -> None:
    nav: NavigateAction = {"action": "navigate", "url": "https://example.com"}
    click: ClickAction = {"action": "click", "selector": "#submit"}
    actions: list[MacroAction] = [nav, click]
    assert [a["action"] for a in actions] == ["navigate", "click"]
