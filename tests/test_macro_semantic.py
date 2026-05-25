# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from octowright.macros.semantic import get_semantic_intent, summarize_action
from octowright.server.macros import macro_explain


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


def test_summarize_wait_for():
    action = {"action": "wait_for", "selector": ".ready"}
    assert summarize_action(action) == "Wait for '.ready' to appear"


def test_summarize_if_selector():
    action = {"action": "if_selector", "selector": ".modal", "then": [{"action": "click", "selector": ".close"}]}
    summary = summarize_action(action)
    assert "If '.modal' is present:" in summary
    assert "  - Click '.close'" in summary


def test_summarize_try():
    action = {"action": "try", "actions": [{"action": "click", "selector": "#cookie-banner"}]}
    summary = summarize_action(action)
    assert "Try (ignore errors):" in summary
    assert "  - Click '#cookie-banner'" in summary


def test_summarize_try_each():
    action = {
        "action": "try_each",
        "branches": [[{"action": "click", "selector": ".v1"}], [{"action": "click", "selector": ".v2"}]],
    }
    summary = summarize_action(action)
    assert "Try each branch until success:" in summary
    assert "  Branch 1:" in summary
    assert "    - Click '.v1'" in summary
    assert "  Branch 2:" in summary
    assert "    - Click '.v2'" in summary


def test_summarize_macro_call():
    action = {"action": "macro_call", "name": "login", "args": {"email": "cosmo", "password": "secret"}}
    summary = summarize_action(action)
    assert summary == "Call macro 'login' with args { email='cosmo', password='secret' }"


def test_summarize_macro_call_without_args():
    action = {"action": "macro_call", "name": "cleanup"}
    summary = summarize_action(action)
    assert summary == "Call macro 'cleanup'"


def test_get_semantic_intent():
    actions = [
        {"action": "navigate", "url": "https://example.com/login"},
        {"action": "fill", "selector": "#user", "value": "ziggy"},
        {"action": "fill", "selector": "#pass", "value": "secret"},
        {"action": "click", "selector": "button#submit"},
    ]
    intent = get_semantic_intent(actions)
    assert "login" in intent.lower()
    assert "ziggy" in intent


async def test_macro_explain():
    actions = [
        {"action": "navigate", "url": "https://example.com"},
        {"action": "click", "selector": "button#ok"},
    ]
    result = await macro_explain(actions)
    assert "summary" in result
    assert "intent" in result
    assert "Navigate to https://example.com" in result["summary"]
    assert "Click 'button#ok'" in result["summary"]
