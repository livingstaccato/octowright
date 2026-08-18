# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""An exact-match flag the tool description never mentions does not exist.

The MCP tool description IS the LLM's documentation -- it never reads the
signature. ``browser_click`` gained a paragraph explaining that matching is
substring by default and that the ``*_exact`` flags also make it
CASE-SENSITIVE; ``browser_fill`` accepts the same flags and said nothing, so
an agent hitting "label='Email' matched 'Email (optional)'" has no discoverable
fix and falls back to a brittle CSS selector.
"""

from __future__ import annotations

import pytest

from octowright.server.browser import input as _input


def _description(name: str) -> str:
    for tool in _input.mcp._tool_manager.list_tools():
        if tool.name == name:
            return tool.description or ""
    pytest.skip(f"{name} not registered under the active profile")


@pytest.mark.parametrize("flag", ["role_exact", "label_exact"])
def test_browser_fill_description_names_its_exact_flags(flag: str) -> None:
    assert flag in _description("browser_fill")


def test_browser_fill_description_warns_that_exact_is_case_sensitive() -> None:
    """The non-obvious half: `label_exact=True` also stops matching on case."""
    assert "CASE-SENSITIVE" in _description("browser_fill")


def test_browser_fill_description_says_matching_is_substring_by_default() -> None:
    assert "SUBSTRING" in _description("browser_fill")


def test_browser_click_description_still_documents_the_same_caveats() -> None:
    """Guards the pair: whichever tool is edited, both stay documented."""
    text = _description("browser_click")
    assert "text_exact" in text and "CASE-SENSITIVE" in text and "SUBSTRING" in text
