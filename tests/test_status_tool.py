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
    assert snap["bridge"]["summary"]["follower_count"] == 1
    assert snap["bridge"]["summary"]["total_in_flight"] == 2
    assert snap["bridge"]["summary"]["total_reconnect_attempts"] == 4
    assert snap["bridge"]["summary"]["total_request_timeouts"] == 1
    assert snap["bridge"]["summary"]["latest_error"] == "remote leader session reset"


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
    # None when the idle-watchdog is disabled (the default); a number when enabled.
    idle_grace = snap["defaults"]["idle_grace_seconds"]
    assert idle_grace is None or isinstance(idle_grace, int | float)
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
    # browser_cap surfaces the pool-wide concurrent-browser cap (int) or None when off.
    assert "browser_cap" in snap["pool"]
    assert snap["pool"]["browser_cap"] is None or isinstance(snap["pool"]["browser_cap"], int)
    # driver_restarts: shared-driver rebuilds after a death (deepest failure signal).
    assert isinstance(snap["pool"]["driver_restarts"], int)
    # crash block surfaces renderer-crash + auto-recovery tallies + recent records.
    crash = snap["crash"]
    assert isinstance(crash["recovery_enabled"], bool)
    assert isinstance(crash["recovery_max"], int)
    for key in ("crashes", "recoveries", "recovery_failures"):
        assert isinstance(crash[key], int)
    assert isinstance(crash["recent"], list)
    assert isinstance(snap["pool"]["driver_restart_recent"], list)
    # health verdict rolls the signals into ok|degraded|critical + reasons.
    assert snap["health"]["status"] in ("ok", "degraded", "critical")
    assert isinstance(snap["health"]["reasons"], list)


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


def test_status_reports_macro_label_metrics(monkeypatch) -> None:
    """Status must surface ``metrics.macro_labels_seen`` + ``macro_label_overflow_count``.

    Operators with a stuck ``_MACRO_LABEL_SEEN`` (e.g. dynamic macro names
    filling the 256-slot cap) need a way to see the current state without
    a daemon restart. The reset escape hatch lives in
    ``macros.execution.reset_macro_label_seen()`` — used by tests or by an
    operator with process access, intentionally NOT exposed as a remote MCP
    tool to keep the surface lean.
    """
    from octowright.macros import execution as _execution

    seen: set[str] = set()
    monkeypatch.setattr(_execution, "_MACRO_LABEL_SEEN", seen)
    monkeypatch.setattr(_execution, "_MACRO_LABEL_OVERFLOW_COUNT", 0)
    monkeypatch.setattr(_execution, "METRICS_MACRO_LABEL_CAP", 2)
    _execution._macro_label("admitted-1")
    _execution._macro_label("admitted-2")
    # These overflow.
    _execution._macro_label("overflow-1")
    _execution._macro_label("overflow-2")
    _execution._macro_label("overflow-3")

    snap = octowright_status()

    assert "metrics" in snap, f"status missing metrics block: {snap}"
    assert snap["metrics"]["macro_labels_seen"] == 2
    # The overflow counter reports admitted-cap overflow attempts (3 here).
    assert snap["metrics"]["macro_label_overflow_count"] == 3
    assert snap["metrics"]["macro_label_cap"] == 2


def test_status_upgrade_block_none_when_no_notice() -> None:
    """No version change recorded → upgrade block is None (not absent)."""
    from octowright.server import _state

    _state.set_upgrade_notice(None)
    snap = octowright_status()
    assert "upgrade" in snap
    assert snap["upgrade"] is None


def test_status_surfaces_upgrade_notice_when_set() -> None:
    """A leader that started after an update surfaces the notice for the agent."""
    from octowright.server import _state

    notice = {
        "kind": "upgrade",
        "previous_version": "0.6.1",
        "current_version": "0.7.0",
        "highlights": ["Offline /new-tab landing page"],
    }
    _state.set_upgrade_notice(notice)
    try:
        snap = octowright_status()
        assert snap["upgrade"] == notice
        # The snapshot is a copy — mutating the status result must not bleed into state.
        snap["upgrade"]["current_version"] = "MUTATED"
        assert _state.upgrade_notice_snapshot()["current_version"] == "0.7.0"
    finally:
        _state.set_upgrade_notice(None)


def test_status_metrics_block_present_when_no_overflow(monkeypatch) -> None:
    """The metrics block must always be present, even when no overflow has happened."""
    from octowright.macros import execution as _execution

    monkeypatch.setattr(_execution, "_MACRO_LABEL_SEEN", set())
    monkeypatch.setattr(_execution, "_MACRO_LABEL_OVERFLOW_COUNT", 0)

    snap = octowright_status()
    assert "metrics" in snap
    assert snap["metrics"]["macro_labels_seen"] == 0
    assert snap["metrics"]["macro_label_overflow_count"] == 0


def test_status_bridge_block_caps_exposed_followers(monkeypatch, tmp_path: Path) -> None:
    """octowright_status must not dump an unbounded followers map — a stale-follower
    leak previously blew the payload past the MCP token limit (242 KB). The exposed
    dict is capped while ``summary.follower_count`` still reports the TRUE total."""
    import json

    from octowright import defaults

    state_path = tmp_path / "bridge-state.json"
    followers = {
        str(pid): {
            "event": "snapshot",
            "follower_pid": pid,
            "ts": float(pid),
            "remote_url": "http://127.0.0.1/mcp/",
            "remote_session_id": f"s{pid}",
            "last_error": None,
            "in_flight": 0,
            "reconnect_attempts": 0,
            "request_timeouts": 0,
        }
        for pid in range(1000, 1060)
    }
    state_path.write_text(json.dumps({"followers": followers, "events": []}))
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", state_path)

    snap = octowright_status()

    assert snap["bridge"]["summary"]["follower_count"] == 60  # true count preserved
    assert len(snap["bridge"]["followers"]) <= 25  # exposed dump bounded
    assert snap["bridge"]["followers_truncated"] is True
