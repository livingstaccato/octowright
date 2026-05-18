# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright_status`` — first-touch session banner snapshot."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from octowright.server.meta import (
    octowright_advisor_record_macro_observation,
    octowright_advisor_set_preference,
    octowright_advisor_status,
    octowright_status,
)


def test_status_returns_required_top_level_blocks() -> None:
    """The status snapshot must include daemon, defaults, pool, personas, dashboard_url."""
    snap = octowright_status()
    for key in ("daemon", "defaults", "pool", "personas", "dashboard_url", "advisor", "bridge"):
        assert key in snap, f"missing top-level field {key!r}: {snap}"


def test_status_includes_octowright_advisor_block(monkeypatch, tmp_path: Path) -> None:
    from octowright import advisor

    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", tmp_path / "advisor.json")
    snap = octowright_status()

    assert snap["advisor"]["name"] == "Octowright Advisor"
    assert snap["advisor"]["preferences"]["macro_candidate"] == "yes"
    assert isinstance(snap["advisor"]["suggestions"], list)


def test_status_includes_bridge_diagnostics(monkeypatch, tmp_path: Path) -> None:
    from octowright import bridge_state, defaults

    state_path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", state_path)
    bridge_state.record_snapshot(
        path=state_path,
        follower_pid=321,
        remote_url="http://127.0.0.1:8765/mcp/",
        remote_session_id="sid-321",
        last_error="remote leader session reset",
        in_flight=2,
        reconnect_attempts=4,
        request_timeouts=1,
    )

    snap = octowright_status()

    assert snap["bridge"]["state_path"] == str(state_path)
    assert snap["bridge"]["followers"]["321"]["remote_session_id"] == "sid-321"
    assert snap["bridge"]["followers"]["321"]["in_flight"] == 2
    assert snap["bridge"]["events"][-1]["last_error"] == "remote leader session reset"


def test_advisor_status_tool_returns_named_status(monkeypatch, tmp_path: Path) -> None:
    from octowright import advisor

    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", tmp_path / "advisor.json")

    snap = octowright_advisor_status()

    assert snap["name"] == "Octowright Advisor"
    assert snap["preferences"]["profile_change"] == "yes"
    assert isinstance(snap["suggestions"], list)


def test_advisor_set_preference_updates_state(monkeypatch, tmp_path: Path) -> None:
    from octowright import advisor

    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", tmp_path / "advisor.json")

    result = octowright_advisor_set_preference("macro_candidate", "automatic")

    assert result["ok"] is True
    assert result["advisor"]["preferences"]["macro_candidate"] == "automatic"
    assert advisor.load_state()["preferences"]["macro_candidate"] == "automatic"


def test_advisor_macro_observation_tool_records_nomination(monkeypatch, tmp_path: Path) -> None:
    from octowright import advisor

    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", tmp_path / "advisor.json")

    octowright_advisor_record_macro_observation(
        source="llm",
        signature="login-flow",
        summary="Repeated login flow with password=hunter2",
    )
    result = octowright_advisor_record_macro_observation(
        source="server",
        signature="login-flow",
        summary="Repeated login flow",
    )

    assert result["ok"] is True
    suggestion = result["advisor"]["suggestions"][0]
    assert suggestion["id"] == "macro-candidate-login-flow"
    assert suggestion["mode"] == "prompt"
    assert advisor.load_state()["macro_observations"][0]["summary"] == "Repeated login flow with password=[redacted]"


def test_advisor_macro_observation_tool_rejects_blank_signature(monkeypatch, tmp_path: Path) -> None:
    from octowright import advisor

    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", tmp_path / "advisor.json")

    with pytest.raises(ValueError, match="signature must not be empty"):
        octowright_advisor_record_macro_observation(source="llm", signature="  ", summary="Repeated flow")


def test_mcp_tools_record_advisor_usage(monkeypatch, tmp_path: Path) -> None:
    from octowright import advisor
    from octowright.server import browser_list

    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", tmp_path / "advisor.json")

    octowright_status()
    browser_list()
    octowright_advisor_status()

    state = advisor.load_state()
    assert [(event["tool"], event["profile"]) for event in state["tool_usage"]] == [("browser_list", "core")]


def test_mcp_tool_usage_tracking_does_not_break_tool_when_advisor_fails(monkeypatch) -> None:
    from octowright import advisor
    from octowright.server import _state

    def fail_record_tool_call(tool_name: str) -> None:
        raise OSError(f"cannot record {tool_name}")

    monkeypatch.setattr(advisor, "record_tool_call", fail_record_tool_call)

    _state._record_advisor_tool_call("browser_list")


def test_status_defaults_block_advertises_persistent_default() -> None:
    """The whole point of the banner — confirm ephemeral_default=False."""
    snap = octowright_status()
    assert snap["defaults"]["ephemeral_default"] is False


def test_status_includes_idle_grace_and_badge_position() -> None:
    snap = octowright_status()
    assert "idle_grace_seconds" in snap["defaults"]
    assert isinstance(snap["defaults"]["idle_grace_seconds"], int | float)
    assert snap["defaults"]["badge_position_default"] == "bottom-right"


def test_status_daemon_block_reports_this_pid() -> None:
    """this_pid must always be the calling process; lets the user/agent
    distinguish 'I am the daemon' from 'I am bridging to a daemon'."""
    snap = octowright_status()
    assert snap["daemon"]["this_pid"] == os.getpid()


def test_status_pool_counts_are_ints() -> None:
    snap = octowright_status()
    assert isinstance(snap["pool"]["live_browsers"], int)
    assert isinstance(snap["pool"]["live_scenarios"], int)
    assert isinstance(snap["pool"]["stale_manifest_count"], int)
    assert isinstance(snap["pool"]["stale_manifest_sessions"], list)


def test_status_personas_returns_name_list() -> None:
    snap = octowright_status()
    assert "names" in snap["personas"]
    assert isinstance(snap["personas"]["names"], list)
    assert snap["personas"]["count"] == len(snap["personas"]["names"])


def test_status_reports_stale_manifest_sessions(monkeypatch, tmp_path: Path) -> None:
    from octowright import session_manifest as _manifest

    manifest_path = tmp_path / "recordings" / "session-manifest.json"
    monkeypatch.setattr(_manifest, "SESSION_MANIFEST_PATH", manifest_path)
    _manifest.record_launch(
        session_id="status-stale",
        kind="chromium",
        label="status",
        profile=None,
        user_data_dir=None,
        log_path=tmp_path / "recordings" / "status-stale.jsonl",
    )

    snap = octowright_status()

    assert snap["pool"]["stale_manifest_count"] == 1
    assert snap["pool"]["stale_manifest_sessions"][0]["session_id"] == "status-stale"


def test_status_caps_returned_stale_manifest_list(monkeypatch, tmp_path: Path) -> None:
    from octowright import session_manifest as _manifest

    manifest_path = tmp_path / "recordings" / "session-manifest.json"
    monkeypatch.setattr(_manifest, "SESSION_MANIFEST_PATH", manifest_path)
    for idx in range(40):
        _manifest.record_launch(
            session_id=f"status-stale-{idx:02d}",
            kind="chromium",
            label=f"status-{idx}",
            profile=None,
            user_data_dir=None,
            log_path=tmp_path / "recordings" / f"status-stale-{idx:02d}.jsonl",
        )

    snap = octowright_status()

    assert snap["pool"]["stale_manifest_count"] == 40
    assert len(snap["pool"]["stale_manifest_sessions"]) == 20
    assert snap["pool"]["stale_manifest_list_truncated"] is True
