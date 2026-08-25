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
from collections.abc import Iterable
from typing import Any

from provide.telemetry import get_logger

log = get_logger("octowright.profiles")

# Meta/diagnostic tools that are always registered regardless of the active
# profile filter. The MCP server's instructions point the LLM at
# ``octowright_status`` to discover the active profile when expected tools
# are missing — filtering it out would defeat that self-awareness hint.
ALWAYS_ON_TOOLS: frozenset[str] = frozenset(
    {
        "octowright_advisor_record_macro_observation",
        "octowright_advisor_set_preference",
        "octowright_advisor_status",
        "octowright_status",
        "octowright_storage_report",
        "octowright_dashboard_url",
        "octowright_check_takeover",
    }
)

PROFILES: dict[str, list[str]] = {
    # Minimum tools needed to drive a browser end-to-end: launch, navigate,
    # interact, observe, close. Pick this when you want the smallest LLM
    # schema that still gets work done.
    "core": [
        "browser_brief",
        "browser_click",
        "browser_close",
        "browser_close_all",
        "browser_set_protected",
        "browser_fields",
        "browser_fill",
        "browser_find_field",
        "browser_find_link",
        "browser_launch",
        "browser_links",
        "browser_list",
        "browser_navigate",
        "browser_page_outline",
        "browser_press_key",
        "browser_quick_launch",
        "browser_read_markdown",
        "browser_screenshot",
        "browser_suggest_for_url",
        "browser_type",
        "browser_wait_for",
        "web_find_links",
        "web_page_outline",
        "web_site_links",
    ],
    # Inspection + assertions + ARIA-locator interactions for stable test
    # automation. Layer on top of `core`.
    "advanced": [
        "capture_cleanup",
        "capture_create",
        "capture_get",
        "capture_lines",
        "capture_list",
        "capture_search",
        "capture_summary",
        "browser_artifact_manifest",
        "browser_console_messages",
        "browser_console_summary",
        "browser_downloads",
        "browser_downloads_summary",
        "browser_each",
        "browser_evaluate",
        "browser_expect_js",
        "browser_expect_selector",
        "browser_expect_text",
        "browser_expect_url",
        "browser_export_script",
        "browser_get_text_by",
        "browser_network_requests",
        "browser_network_summary",
        "browser_observe",
        "browser_relaunch_fluid",
        "browser_resize",
        "browser_recording_path",
        "browser_snapshot",
        "browser_tail_recording",
        "browser_wait_for_download",
        "browser_viewport_status",
        "browser_viewport_sync",
    ],
    # Macro recording, replay, lint, repair, compile.
    "macros": [
        "macro_artifact_list",
        "macro_artifact_plan",
        "macro_artifact_run",
        "macro_compile",
        "macro_delete",
        "macro_digest",
        "macro_explain",
        "macro_export_cli",
        "macro_lint",
        "macro_list",
        "macro_repair_apply",
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
    # Accessibility-tree snapshot save/diff/verify. Pair with `advanced` for
    # full test-automation workflows.
    "goldens": [
        "golden_assert",
        "golden_delete",
        "golden_list",
        "golden_save",
        "golden_verify_loop",
    ],
    # No "terminals" entry: that profile is the terminal PLUGIN's, declared by
    # its descriptor's `profile_name` and registered through
    # `register_plugin_profile` when it loads. Core reserving the name here made
    # the plugin unloadable -- `register_plugin_profile` refuses any name already
    # in PROFILES, so activation failed with a profile collision and the kind
    # never registered at all. The profile itself is unchanged: the plugin
    # declares the same seven tools this entry used to list.
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

#: Capability profiles contributed by enabled plugins. Kept separate from
#: PROFILES so core's static table stays static and a plugin cannot mutate a
#: core profile's membership. Populated by the loader BEFORE the active filter
#: is computed — the ordering is load-bearing, see build_allowed_set.
_PLUGIN_PROFILES: dict[str, frozenset[str]] = {}


def register_plugin_profile(name: str, tool_names: Iterable[str]) -> None:
    """Register a plugin's capability profile.

    A plugin may create a profile; it may not extend or shadow a core one,
    because a third-party package silently widening ``core`` would defeat the
    point of picking a narrow profile.
    """
    if name in PROFILES:
        raise ValueError(f"plugin profile {name!r} collides with a core profile")
    _PLUGIN_PROFILES[name] = frozenset(tool_names)


def unregister_plugin_profile(name: str) -> None:
    """Remove a plugin's capability profile.

    Idempotent — a name that was never registered is not an error, because the
    loader's rollback path runs for failures that happened before registration
    as well as after. Called when a plugin's activation is rolled back: leaving
    the profile behind would make ``OCTOWRIGHT_PROFILE=<its name>`` resolve to
    a set naming tools that do not exist.
    """
    _PLUGIN_PROFILES.pop(name, None)


def plugin_profile_names() -> list[str]:
    return sorted(_PLUGIN_PROFILES)


def reset_plugin_profiles() -> None:
    """Clear registered plugin profiles. Test seam; the daemon never calls it."""
    _PLUGIN_PROFILES.clear()


def build_allowed_set(profile_spec: str) -> set[str]:
    """Resolve a comma-separated profile spec to the set of allowed tool names.

    Resolves names against both core :data:`PROFILES` and plugin-contributed
    profiles registered via :func:`register_plugin_profile`. Unknown profile
    names are logged at WARNING and skipped — a typo in the profile spec
    would otherwise silently produce an unexpectedly narrow surface. If EVERY
    name was unknown (so the resolved set is exactly :data:`ALWAYS_ON_TOOLS`
    despite a non-empty spec), an additional ERROR-level log fires so the
    operator notices the daemon-is-healthy-but-LLM-has-no-tools failure mode
    instead of chasing a "why does my MCP client not see browser_launch?" thread.

    Callers that want "no filter" should detect that themselves via
    :func:`active_filter` returning ``None``.
    """
    names = [p.strip() for p in profile_spec.split(",") if p.strip()]
    allowed: set[str] = set(ALWAYS_ON_TOOLS)
    matched_any = False
    for name in names:
        if name in PROFILES:
            matched_any = True
            allowed.update(PROFILES[name])
            continue
        if name in _PLUGIN_PROFILES:
            matched_any = True
            allowed.update(_PLUGIN_PROFILES[name])
            continue
        # Diagnose against BOTH tables. Checking PROFILES alone made
        # `OCTOWRIGHT_PROFILE=<plugin profile>` log profile.unknown and then
        # profile.all_unknown at ERROR — the loudest signal the daemon emits,
        # fired at a correct configuration.
        log.warning(
            "octowright.profile.unknown",
            profile=name,
            known=sorted([*PROFILES.keys(), *_PLUGIN_PROFILES.keys()]),
        )
    if names and not matched_any:
        log.error(
            "octowright.profile.all_unknown",
            profile_spec=profile_spec,
            known=sorted([*PROFILES.keys(), *_PLUGIN_PROFILES.keys()]),
            hint=(
                "every profile name was unknown — the daemon will start "
                "with only ALWAYS_ON_TOOLS (no browser tools at all). "
                "Set OCTOWRIGHT_PROFILE to a known profile or unset for the full surface."
            ),
        )
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


def profiles_for_tool(tool_name: str) -> list[str]:
    return sorted(name for name, tools in PROFILES.items() if tool_name in tools)


def annotate_next_actions_for_profile(
    actions: Any,
    env: dict[str, str] | None = None,
) -> Any:
    """Mark follow-up actions that are hidden by the active profile.

    ``next_actions`` are guidance, not tool registration. Under a slim
    profile, keeping a useful but unavailable follow-up is still valuable
    if the agent can see it requires another profile instead of blindly
    calling a hidden tool.
    """
    allowed = active_filter(env)
    if allowed is None:
        return actions

    annotated: list[dict[str, Any]] = []
    for action in actions:
        tool_name = str(action.get("tool") or "")
        if tool_name in allowed:
            annotated.append(action)
            continue
        copied = dict(action)
        copied["available"] = False
        profiles = profiles_for_tool(tool_name)
        if profiles:
            copied["requires_profile"] = profiles[0]
            copied["available_profiles"] = profiles
        annotated.append(copied)
    return annotated
