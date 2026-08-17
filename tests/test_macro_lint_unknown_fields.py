# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_lint`` must reject fields the runtime cannot dispatch.

``_SIMPLE_REQUIRED`` encodes only REQUIRED fields, so lint had nothing to say
about a field that is merely *wrong*. ``runtime._dispatch_standard`` then
splats ``**kwargs`` straight at the session method, so an unknown key is a
``TypeError`` on the first LIVE run -- potentially days after authoring, and
after the macro has already performed its earlier side effects.

The concrete report: a macro used ``wait_for`` with a ``js:`` field, copying
the live tool's JS-condition idea but not its parameter name. The supported
spelling is ``expression``. Lint had the signature available the whole time.

The allowed set is DERIVED from the dispatch target's real signature rather
than hand-mirrored, so it cannot drift the way a copied table would.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.lint import lint_macro
from octowright.macros.runtime import _ACTION_MAP


def _macro(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": "m", "actions": actions}


def _codes(action: dict[str, Any]) -> list[str]:
    return [i.code for i in lint_macro(_macro([action]))]


def test_unknown_field_on_wait_for_is_reported() -> None:
    assert "unknown_field" in _codes({"action": "wait_for", "js": "document.title"})


def test_the_supported_js_spelling_is_accepted() -> None:
    """`expression` is the real parameter and must NOT be flagged."""
    assert "unknown_field" not in _codes({"action": "wait_for", "expression": "document.title"})


def test_unknown_field_message_names_the_field_and_the_action() -> None:
    issues = lint_macro(_macro([{"action": "wait_for", "js": "x"}]))
    unknown = [i for i in issues if i.code == "unknown_field"]
    assert len(unknown) == 1
    assert "js" in unknown[0].message
    assert "wait_for" in unknown[0].message


def test_unknown_field_is_an_error_not_a_warning() -> None:
    """It is a guaranteed runtime TypeError, not a style opinion."""
    issues = lint_macro(_macro([{"action": "wait_for", "js": "x"}]))
    assert [i.severity for i in issues if i.code == "unknown_field"] == ["error"]


@pytest.mark.parametrize(
    "action",
    [
        {"action": "navigate", "url": "https://example.com"},
        {"action": "click", "selector": "#go"},
        {"action": "type", "selector": "#q", "text": "hi", "delay_ms": 0},
        {"action": "fill", "selector": "#q", "value": "hi"},
        {"action": "press_key", "key": "Enter"},
        {"action": "evaluate", "expression": "1+1"},
        {"action": "wait_for", "selector": "#x", "timeout_ms": 100},
        {"action": "expect_text", "selector": "#x", "text": "hi"},
        {"action": "mock_route", "pattern": "**/api", "status": 200},
        {"action": "drag", "source": "#a", "target": "#b"},
        {"action": "click_by", "role": "button", "role_name": "Save", "role_exact": True},
        {"action": "click_by", "text": "Ada", "text_exact": True},
        {"action": "fill_by", "label": "Email", "label_exact": True, "value": "x"},
        {"action": "get_text_by", "test_id": "total", "result": "7"},
        {"action": "switch_frame", "selector": "#f", "index": 0},
        {"action": "open_url", "url": "https://example.com", "page_index": 1},
        {"action": "navigate_back", "url": "https://example.com"},
    ],
)
def test_realistic_recorded_actions_produce_no_unknown_field(action: dict[str, Any]) -> None:
    """Every shape the recorder actually writes must lint clean.

    A false positive here is worse than the original gap: it would make lint
    reject working macros, so this covers the recorded-only fields that the
    runtime drops (`page_index`, `result`, …) and the renamed ones (`pattern`,
    `source`/`target`) alongside ordinary parameters.
    """
    assert "unknown_field" not in _codes(action)


def test_every_dispatchable_action_has_a_derived_allowed_set() -> None:
    """Drift guard: a new action in the dispatch map must resolve a signature.

    If someone adds an action whose allowed fields can't be derived, this fails
    rather than silently letting every field through for that action.
    """
    from octowright.macros.lint_fields import allowed_fields_for

    for kind in _ACTION_MAP:
        allowed = allowed_fields_for(kind)
        assert allowed, f"no allowed-field set derived for {kind!r}"
