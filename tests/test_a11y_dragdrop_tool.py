# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations


def test_tool_is_importable_from_the_browser_package() -> None:
    """A new tool module that nobody imports registers nothing.

    `server/browser/__init__.py` imports each submodule for its decorator side
    effect; a module left out of that list has its `@mcp.tool` never run, so
    the tool silently does not exist at runtime while every unit test passes.
    """
    from octowright.server.browser import browser_a11y_dragdrop

    assert callable(browser_a11y_dragdrop)
