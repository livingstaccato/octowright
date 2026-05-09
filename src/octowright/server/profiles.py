# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""MCP-tool capability profiles.

Profiles let an operator slim the LLM-visible tool surface at server start
(via ``OCTOWRIGHT_PROFILE`` or ``octowright serve --profile=...``) so the
schema the LLM consumes only includes the tools they actually want. The
filter is applied at ``@mcp.tool`` decoration time in ``server/_state``;
tools whose name is not in any active profile are skipped entirely.

When the env var is unset (or set to ``all``), every tool registers — that
is the back-compat default.
"""

from __future__ import annotations

import os

PROFILES: dict[str, list[str]] = {
    "core": [
        "browser_click",
        "browser_type",
        "browser_fill",
        "browser_launch",
        "browser_close",
        "browser_navigate",
        "browser_brief",
        "browser_wait_for",
        "browser_read_markdown",
    ],
    "advanced": [
        "browser_snapshot",
        "browser_evaluate",
        "browser_console_messages",
        "browser_expect_text",
        "browser_expect_url",
    ],
}


def build_allowed_set(profile_spec: str) -> set[str]:
    """Resolve a comma-separated profile spec to the set of allowed tool names.

    Unknown profile names are silently ignored. An empty result keeps no
    tools — callers that want "no filter" should detect that themselves
    via :func:`active_filter` returning ``None``.
    """
    names = [p.strip() for p in profile_spec.split(",") if p.strip()]
    allowed: set[str] = set()
    for name in names:
        allowed.update(PROFILES.get(name, []))
    return allowed


def active_filter(env: dict[str, str] | None = None) -> set[str] | None:
    """Return the active allow-list, or ``None`` for "register everything".

    ``OCTOWRIGHT_PROFILE`` unset or set to ``all`` (case-insensitive) means
    no filtering. Any other value is parsed as a comma-separated profile
    spec via :func:`build_allowed_set`.
    """
    raw = (env if env is not None else os.environ).get("OCTOWRIGHT_PROFILE", "").strip()
    if not raw or raw.lower() == "all":
        return None
    return build_allowed_set(raw)
