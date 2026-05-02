# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from octowright.server import registered_tool_names


def test_macro_explain_registered():
    tools = registered_tool_names()
    assert "macro_explain" in tools
