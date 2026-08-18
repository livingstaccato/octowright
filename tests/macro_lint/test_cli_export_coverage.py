# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exported CLI must dispatch every action kind replay can dispatch.

``render_macro_cli`` emits an ``if/elif kind == ...`` chain ending in
``raise RuntimeError("unsupported macro action in exported CLI")``. Three
branches were added by hand for the semantic-locator family, which fixed those
three kinds and left the chain covering 13 of ``_ACTION_MAP``'s 29 — so
``macro_export_cli`` on a macro containing an ordinary ``hover``, ``evaluate``
or ``screenshot`` still aborted at runtime.

The test that came with those three branches asserted three specific branch
strings, which is why the next missing kind would have landed the same way.
This one asserts the chain against ``_ACTION_MAP`` itself, so adding a
dispatchable action without an export branch fails here instead of in a user's
generated script.
"""

from __future__ import annotations

import pytest

from octowright.artifacts.script_export_actions import EXPORT_UNSUPPORTED, exported_action_kinds
from octowright.macros.runtime import _ACTION_MAP


def test_every_dispatchable_action_has_an_export_branch() -> None:
    missing = sorted(set(_ACTION_MAP) - exported_action_kinds())
    assert missing == [], f"exported CLI cannot run these replayable actions: {missing}"


def test_export_declares_nothing_the_runtime_cannot_replay() -> None:
    """Drift in the other direction: a branch for a kind replay never produces."""
    extra = sorted(exported_action_kinds() - set(_ACTION_MAP))
    assert extra == [], f"exported CLI dispatches kinds the runtime does not: {extra}"


@pytest.mark.parametrize(
    "action",
    [
        {"action": "hover", "selector": "#menu"},
        {"action": "evaluate", "expression": "1 + 1"},
        {"action": "screenshot", "path": "shot.png"},
        {"action": "select_option", "selector": "#country", "value": "NL"},
        {"action": "drag", "source": "#a", "target": "#b"},
        {"action": "resize", "width": 1280, "height": 800},
        {"action": "navigate_back"},
        {"action": "open_url", "url": "https://example.com"},
        {"action": "switch_page", "index": 1},
        {"action": "close_page", "index": 1},
        {"action": "switch_frame", "selector": "iframe#pay"},
        {"action": "reset_frame"},
        {"action": "mock_route", "pattern": "**/api/*", "status": 200},
        {"action": "unmock_route", "pattern": "**/api/*"},
        {"action": "set_dialog_policy", "policy": "accept"},
        {"action": "set_input_files", "selector": "#file", "files": ["a.txt"]},
    ],
)
def test_previously_unsupported_kinds_now_render(action: dict[str, object]) -> None:
    from octowright.artifacts.script_export import render_macro_cli

    script = render_macro_cli(name="m", macro={"actions": [action]}, include_evidence=False)
    assert f'elif kind == "{action["action"]}":' in script
    compile(script, "<exported>", "exec")


def test_the_unsupported_raise_is_still_the_fallthrough() -> None:
    """The chain must keep failing loudly on a kind it genuinely cannot run."""
    from octowright.artifacts.script_export import render_macro_cli

    script = render_macro_cli(name="m", macro={"actions": []}, include_evidence=False)
    assert EXPORT_UNSUPPORTED in script
