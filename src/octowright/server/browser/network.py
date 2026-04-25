# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Network/dialog tools: dialog policies + route mocking."""

from __future__ import annotations

from typing import Any

from .._state import mcp, pool


@mcp.tool(
    structured_output=False,
    description=(
        "Set the dialog-handling policy for an instance. `policy` is 'accept', 'dismiss', "
        "or 'manual'. When 'accept' is used with a prompt dialog, `prompt_text` supplies "
        "the response string. Default policy is 'dismiss'."
    ),
)
def browser_set_dialog_policy(
    instance_id: str,
    policy: str,
    prompt_text: str | None = None,
) -> dict[str, Any]:
    return pool.get(instance_id).set_dialog_policy(policy, prompt_text)


@mcp.tool(
    structured_output=False,
    description=(
        "Stub network responses for requests matching `url_pattern`. The browser will see "
        "your response instead of hitting the network. Use this to make tests deterministic "
        "(freeze a /api/time endpoint, return a fixed user list, simulate a 500 error). "
        "Don't use this to OBSERVE traffic — it short-circuits the request. "
        "`url_pattern` is a glob ('**/api/users') or regex (Playwright auto-detects)."
    ),
)
async def browser_mock_route(
    instance_id: str,
    url_pattern: str,
    status: int = 200,
    body: str | None = None,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).mock_route(
        url_pattern,
        status=status,
        body=body,
        content_type=content_type,
        headers=headers,
    )


@mcp.tool(
    structured_output=False,
    description=("Remove a previously-installed mock for `url_pattern`. Raises if no mock was active."),
)
async def browser_unmock_route(instance_id: str, url_pattern: str) -> dict[str, Any]:
    return await pool.get(instance_id).unmock_route(url_pattern)
