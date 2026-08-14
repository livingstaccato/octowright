# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Schema-regression pin for the three session methods Task 5 made async
(``page_list``, ``browser_list_frames``, ``browser_set_dialog_policy``).

Task 10 wraps their MCP-tool bodies in ``browser_operation(...)`` boundaries
(complete-workflow gating), which must not change what the MCP runtime
advertises to a client: same ``name``/``description``/``parameters``, still
``structured_output=False`` (``tool.fn_metadata.wrap_output is False`` in the
installed MCP runtime's representation), and still no ``output_schema``. A
change here would mean the wrapping altered the tool's public contract, not
just its internal serialization.
"""

from __future__ import annotations

from typing import Any

from octowright.server._state import mcp

_PINNED: dict[str, dict[str, Any]] = {
    "page_list": {
        "description": (
            "List all pages/tabs for an instance. The active page (the one every other "
            "per-instance tool targets) has is_active=True. Popups opened by the browser "
            "are tracked automatically and appear here. Pass response_mode='summary' for "
            "bounded rows with page_switch/page_close action payloads."
        ),
        "parameters": {
            "properties": {
                "instance_id": {"title": "Instance Id", "type": "string"},
                "response_mode": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "title": "Response Mode",
                },
                "limit": {"default": 20, "title": "Limit", "type": "integer"},
            },
            "required": ["instance_id"],
            "title": "page_listArguments",
            "type": "object",
        },
    },
    "browser_list_frames": {
        "description": (
            "List all frames on the active page (including main). Pass response_mode='summary' "
            "for bounded rows with browser_switch_frame/browser_reset_frame action payloads."
        ),
        "parameters": {
            "properties": {
                "instance_id": {"title": "Instance Id", "type": "string"},
                "response_mode": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "title": "Response Mode",
                },
                "limit": {"default": 20, "title": "Limit", "type": "integer"},
            },
            "required": ["instance_id"],
            "title": "browser_list_framesArguments",
            "type": "object",
        },
    },
    "browser_set_dialog_policy": {
        "description": (
            "Set the dialog-handling policy for an instance. `policy` is 'accept', 'dismiss', "
            "or 'manual'. When 'accept' is used with a prompt dialog, `prompt_text` supplies "
            "the response string. Default policy is 'dismiss'."
        ),
        "parameters": {
            "properties": {
                "instance_id": {"title": "Instance Id", "type": "string"},
                "policy": {"title": "Policy", "type": "string"},
                "prompt_text": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "title": "Prompt Text",
                },
            },
            "required": ["instance_id", "policy"],
            "title": "browser_set_dialog_policyArguments",
            "type": "object",
        },
    },
}


def test_gated_session_method_tool_schemas_are_unchanged() -> None:
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    for name, expected in _PINNED.items():
        tool = tools[name]
        assert tool.name == name
        assert tool.description == expected["description"]
        assert tool.parameters == expected["parameters"]
        assert tool.fn_metadata.wrap_output is False
        assert tool.output_schema is None
