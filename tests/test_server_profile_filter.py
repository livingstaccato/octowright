# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Capability-profile filter tests.

Unit-level tests cover `build_allowed_set` / `active_filter` directly. The
integration tests run a fresh subprocess so toggling `OCTOWRIGHT_PROFILE`
does not destabilise the in-process server singletons that the rest of
the suite relies on.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from octowright.server import profiles


def test_active_filter_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
    assert profiles.active_filter() is None


def test_active_filter_all_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "all")
    assert profiles.active_filter() is None


def test_active_filter_all_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "ALL")
    assert profiles.active_filter() is None


def test_build_allowed_set_single_profile() -> None:
    allowed = profiles.build_allowed_set("core")
    assert allowed == set(profiles.PROFILES["core"]) | set(profiles.ALWAYS_ON_TOOLS)


def test_build_allowed_set_multiple_profiles_union() -> None:
    allowed = profiles.build_allowed_set("core,advanced")
    expected = set(profiles.PROFILES["core"]) | set(profiles.PROFILES["advanced"]) | set(profiles.ALWAYS_ON_TOOLS)
    assert allowed == expected


def test_browser_observe_is_advanced_not_core() -> None:
    assert "browser_observe" not in profiles.PROFILES["core"]
    assert "browser_observe" in profiles.PROFILES["advanced"]


def test_advanced_profile_includes_diagnostic_raw_followups() -> None:
    assert "browser_network_summary" in profiles.PROFILES["advanced"]
    assert "browser_network_requests" in profiles.PROFILES["advanced"]
    assert "browser_downloads_summary" in profiles.PROFILES["advanced"]
    assert "browser_downloads" in profiles.PROFILES["advanced"]
    assert "browser_wait_for_download" in profiles.PROFILES["advanced"]


def test_core_outline_marks_advanced_capture_followup_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server.browser import inspect as _inspect

    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "core")

    actions = _inspect._outline_next_actions("i")
    capture_action = next(action for action in actions if action["tool"] == "capture_create")

    assert capture_action["available"] is False
    assert capture_action["requires_profile"] == "advanced"


def test_build_allowed_set_unknown_profile_ignored() -> None:
    allowed = profiles.build_allowed_set("core,bogus")
    assert allowed == set(profiles.PROFILES["core"]) | set(profiles.ALWAYS_ON_TOOLS)


def test_build_allowed_set_empty_spec_yields_meta_tools_only() -> None:
    assert profiles.build_allowed_set("") == set(profiles.ALWAYS_ON_TOOLS)
    assert profiles.build_allowed_set(",,,") == set(profiles.ALWAYS_ON_TOOLS)


def test_build_allowed_set_all_unknown_emits_error_log(caplog: pytest.LogCaptureFixture) -> None:
    """When EVERY profile name in the spec is unknown, the resolved set is
    just ALWAYS_ON_TOOLS — the daemon starts healthy but the LLM sees zero
    browser tools. An ERROR-level log distinguishes that footgun from a
    deliberate `OCTOWRIGHT_PROFILE=""` (which the empty-spec test above
    pins as the "meta tools only" path)."""
    import logging

    with caplog.at_level(logging.ERROR, logger="octowright.server.profiles"):
        allowed = profiles.build_allowed_set("bogus1,bogus2")
    assert allowed == set(profiles.ALWAYS_ON_TOOLS)
    assert any("octowright.profile.all_unknown" in rec.message for rec in caplog.records)


def test_build_allowed_set_partial_unknown_does_not_trigger_all_unknown_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One known + one unknown produces the per-name WARNING but NOT the
    all-unknown ERROR — the resolved set still has a real profile in it."""
    import logging

    with caplog.at_level(logging.WARNING, logger="octowright.server.profiles"):
        profiles.build_allowed_set("core,bogus")
    assert not any("octowright.profile.all_unknown" in rec.message for rec in caplog.records)


def test_meta_tools_always_present_under_core_profile() -> None:
    """`octowright_status` must remain reachable so the LLM can discover the
    active profile when expected tools are missing."""
    allowed = profiles.build_allowed_set("core")
    assert "octowright_status" in allowed
    assert "octowright_dashboard_url" in allowed
    assert "octowright_advisor_status" in allowed
    assert "octowright_advisor_set_preference" in allowed
    assert "octowright_advisor_record_macro_observation" in allowed


def _registered_names_in_subprocess(env_value: str | None) -> set[str]:
    """Run a fresh Python interpreter, optionally setting OCTOWRIGHT_PROFILE,
    import the server, and print its registered tool names as JSON. Avoids
    the singleton-reload pitfalls of an in-process reload."""
    import os

    code = textwrap.dedent(
        """
        import json
        from octowright.server import registered_tool_names
        print(json.dumps(sorted(registered_tool_names())))
        """
    )
    # Inherit the parent environment (Windows requires SYSTEMROOT, PATHEXT,
    # TEMP, etc. for Python to even start), then set/unset OCTOWRIGHT_PROFILE.
    env = os.environ.copy()
    env.pop("OCTOWRIGHT_PROFILE", None)
    if env_value is not None:
        env["OCTOWRIGHT_PROFILE"] = env_value
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return set(json.loads(result.stdout))


def test_profile_core_subprocess_filters_tools() -> None:
    names = _registered_names_in_subprocess("core")
    expected = set(profiles.PROFILES["core"])
    assert expected.issubset(names), f"missing core tools: {expected - names}"
    assert "browser_suggest_for_url" in names
    assert "browser_quick_launch" in names
    assert "browser_links" in names
    assert "browser_find_link" in names
    assert "browser_fields" in names
    assert "browser_find_field" in names
    assert "browser_page_outline" in names
    assert "web_page_outline" in names
    assert "web_find_links" in names
    assert "web_site_links" in names
    assert "octowright_advisor_status" in names
    assert "octowright_advisor_set_preference" in names
    assert "octowright_advisor_record_macro_observation" in names
    for absent in ("browser_snapshot", "scenario_start", "macro_save", "persona_list"):
        assert absent not in names, f"{absent} should not register under profile=core"


def test_profile_unset_subprocess_registers_full_surface() -> None:
    names = _registered_names_in_subprocess(None)
    for tool in ("browser_snapshot", "scenario_start", "macro_save", "persona_list"):
        assert tool in names, f"{tool} must register when no profile is set"


def test_profile_all_subprocess_registers_full_surface() -> None:
    names = _registered_names_in_subprocess("all")
    for tool in ("browser_snapshot", "scenario_start", "macro_save", "persona_list"):
        assert tool in names, f"{tool} must register when profile=all"


def test_profile_core_advanced_subprocess_combines() -> None:
    names = _registered_names_in_subprocess("core,advanced")
    union = set(profiles.PROFILES["core"]) | set(profiles.PROFILES["advanced"])
    assert union.issubset(names)
    assert "capture_summary" in names
    assert "capture_lines" in names
    assert "browser_console_summary" in names
    assert "browser_downloads_summary" in names
    assert "browser_network_summary" in names
    # Things outside both profiles still excluded.
    assert "scenario_start" not in names


def test_profile_macros_subprocess_isolates_macros() -> None:
    names = _registered_names_in_subprocess("macros")
    assert set(profiles.PROFILES["macros"]).issubset(names)
    # Browser surface stays excluded under macros-only.
    assert "browser_launch" not in names
    assert "scenario_start" not in names


def test_profile_scenarios_subprocess_isolates_scenarios() -> None:
    names = _registered_names_in_subprocess("scenarios")
    assert set(profiles.PROFILES["scenarios"]).issubset(names)
    assert "macro_save" not in names
    assert "browser_launch" not in names


def test_profile_personas_subprocess_isolates_personas() -> None:
    names = _registered_names_in_subprocess("personas")
    assert set(profiles.PROFILES["personas"]).issubset(names)
    assert "browser_launch" not in names
    assert "macro_save" not in names


def test_profiled_mcpserver_honours_explicit_name_override_in_allowlist() -> None:
    """An MCP tool whose Python `__name__` is NOT in any profile but whose
    explicit `name=` override IS in the allowlist must still register.
    Otherwise renaming via `@mcp.tool(name=...)` is a silent profile-filter bypass.
    """
    from octowright.server._state import _ProfiledMCPServer

    server = _ProfiledMCPServer("octowright-test", allowed_tools={"public_alias"})

    @server.tool(name="public_alias")
    async def _internal_handler() -> dict[str, str]:
        return {"ok": "yes"}

    registered = {tool.name for tool in server._tool_manager.list_tools()}
    assert "public_alias" in registered
    assert "_internal_handler" not in registered


def test_profiled_mcpserver_filters_out_when_name_override_missing_from_allowlist() -> None:
    """The inverse: a Python `__name__` that matches the allowlist must NOT
    rescue a tool whose explicit `name=` override is outside it. The MCP-visible
    name is the authority.
    """
    from octowright.server._state import _ProfiledMCPServer

    server = _ProfiledMCPServer("octowright-test", allowed_tools={"browser_launch"})

    @server.tool(name="hidden_tool")
    async def browser_launch() -> dict[str, str]:
        return {"ok": "yes"}

    registered = {tool.name for tool in server._tool_manager.list_tools()}
    assert "hidden_tool" not in registered
    assert "browser_launch" not in registered
