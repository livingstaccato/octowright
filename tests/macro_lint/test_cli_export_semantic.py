# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_export_cli`` must be able to export a semantic-locator macro.

The generated script dispatched on action kind with an ``else: raise
RuntimeError("unsupported macro action in exported CLI")``, and had branches
only for the CSS-selector actions. ``click_by`` / ``fill_by`` / ``get_text_by``
have been dispatchable macro actions all along, and 0.14.4's whole headline
feature (``text_exact`` / ``label_exact``) only exists on that family -- so the
one export path that turns a macro into a standalone runnable script aborted on
exactly the macros the release was about.
"""

from __future__ import annotations

import pytest

from octowright.artifacts.script_export import render_macro_cli


@pytest.fixture
def script() -> str:
    actions = [
        {"action": "navigate", "url": "https://example.com"},
        {"action": "click_by", "text": "Buy now", "text_exact": True},
        {"action": "fill_by", "label": "Email", "label_exact": True, "value": "a@b.c"},
        {"action": "click_by", "role": "button", "role_name": "Save", "role_exact": True},
        {"action": "click_by", "test_id": "submit"},
        {"action": "get_text_by", "test_id": "total"},
    ]
    return render_macro_cli(name="semantic-demo", macro={"actions": actions}, include_evidence=False)


@pytest.mark.parametrize("kind", ["click_by", "fill_by", "get_text_by"])
def test_generated_script_dispatches_the_semantic_locator_family(script: str, kind: str) -> None:
    """Asserting on the DISPATCH branch, not on the kind name: every kind also
    appears verbatim in the script's embedded ACTIONS json, so a substring
    check on the name alone passes even when the branch is missing."""
    assert f'elif kind == "{kind}":' in script, f"{kind} falls through to the unsupported-action raise"


@pytest.mark.parametrize(
    "call",
    ["get_by_role", "get_by_label", "get_by_text", "get_by_test_id"],
)
def test_generated_script_can_build_every_finder(script: str, call: str) -> None:
    assert call in script


def test_generated_script_forwards_the_exact_modifiers(script: str) -> None:
    """Dropping `exact` silently changes which element the script acts on."""
    assert "exact=" in script


def test_generated_script_is_valid_python(script: str) -> None:
    compile(script, "<exported>", "exec")
