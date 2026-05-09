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
    # Minimum tools needed to drive a browser end-to-end: launch, navigate,
    # interact, observe, close. Pick this when you want the smallest LLM
    # schema that still gets work done.
    "core": [
        "browser_brief",
        "browser_click",
        "browser_close",
        "browser_close_all",
        "browser_fill",
        "browser_launch",
        "browser_list",
        "browser_navigate",
        "browser_press_key",
        "browser_read_markdown",
        "browser_screenshot",
        "browser_type",
        "browser_wait_for",
    ],
    # Inspection + assertions + ARIA-locator interactions for stable test
    # automation. Layer on top of `core`.
    "advanced": [
        "browser_click_by",
        "browser_console_messages",
        "browser_evaluate",
        "browser_expect_js",
        "browser_expect_selector",
        "browser_expect_text",
        "browser_expect_url",
        "browser_export_script",
        "browser_fill_by",
        "browser_get_text_by",
        "browser_recording_path",
        "browser_snapshot",
        "browser_tail_recording",
    ],
    # Macro recording, replay, lint, repair, compile.
    "macros": [
        "macro_compile",
        "macro_delete",
        "macro_explain",
        "macro_lint",
        "macro_list",
        "macro_repair_preview",
        "macro_run",
        "macro_run_sequence",
        "macro_save",
    ],
    # Scenario orchestration (multi-browser test setups).
    "scenarios": [
        "scenario_list",
        "scenario_participants",
        "scenario_plan",
        "scenario_remap_participants",
        "scenario_run_as_test",
        "scenario_run_macro",
        "scenario_spawn_template",
        "scenario_start",
        "scenario_status",
        "scenario_stop",
        "scenario_tail",
        "scenario_wait_for_sync",
    ],
    # Persona + on-disk profile management.
    "personas": [
        "persona_create",
        "persona_credentials_check",
        "persona_delete",
        "persona_get",
        "persona_list",
        "profile_cleanup",
        "profile_delete",
        "profile_list",
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
