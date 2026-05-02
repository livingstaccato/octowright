# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from octowright.server.macro_semantic import summarize_action


def test_summarize_navigate():
    action = {"action": "navigate", "url": "https://google.com"}
    assert summarize_action(action) == "Navigate to https://google.com"


def test_summarize_click():
    action = {"action": "click", "selector": "button#login"}
    assert summarize_action(action) == "Click 'button#login'"


def test_summarize_type():
    action = {"action": "type", "selector": "input#email", "text": "user@example.com"}
    assert summarize_action(action) == "Type 'user@example.com' into 'input#email'"


def test_summarize_fill():
    action = {"action": "fill", "selector": "input#password", "value": "hunter2"}
    assert summarize_action(action) == "Fill 'input#password' with 'hunter2'"
