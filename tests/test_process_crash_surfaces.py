# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Where a browser-process crash is visible: push and pull."""

from __future__ import annotations

from octowright.browser_pool import incidents
from octowright.browser_pool.events import SessionCrashedEvent
from octowright.server.mcp_notifications import notification_payload


def _hint(scope: str, recovering: bool) -> str:
    event = SessionCrashedEvent("p1", "chromium", "lbl", "persona", scope, "/tmp/p1.jsonl", recovering=recovering)  # type: ignore[arg-type]
    return str(notification_payload(event)["params"]["hint"])


def test_a_process_crash_hint_says_the_browser_itself_died() -> None:
    hint = _hint("process", False)

    assert "browser process" in hint
    assert "browser_launch" in hint
    # Not the renderer wording: there is no page to recover in a dead context.
    assert hint != _hint("renderer", False)


def test_a_relaunching_process_crash_points_at_the_lost_session_mapping() -> None:
    hint = _hint("process", True)

    assert "lost_sessions" in hint
    assert hint != _hint("renderer", True)


def test_status_lists_process_crashes_under_crash_recent() -> None:
    from octowright.server.meta import octowright_status

    incidents.reset()
    incidents.record(incidents.CATEGORY_RENDERER_CRASH, instance_id="r1", outcome="recovered")
    incidents.record(incidents.CATEGORY_BROWSER_PROCESS_CRASH, instance_id="p1", outcome="lost")
    incidents.record(incidents.CATEGORY_UNRESPONSIVE_TARGET, instance_id="u1", operation="browser_evaluate")

    crash = octowright_status()["crash"]

    assert [r["instance_id"] for r in crash["recent"]] == ["r1", "p1"]
    assert [r["instance_id"] for r in crash["unresponsive_recent"]] == ["u1"]
    incidents.reset()


def test_incidents_can_be_read_for_a_set_of_categories() -> None:
    incidents.reset()
    incidents.record(incidents.CATEGORY_RENDERER_CRASH, instance_id="r1")
    incidents.record(incidents.CATEGORY_UNRESPONSIVE_TARGET, instance_id="u1")
    incidents.record(incidents.CATEGORY_BROWSER_PROCESS_CRASH, instance_id="p1")
    incidents.record(incidents.CATEGORY_RENDERER_CRASH, instance_id="r2")

    both = {incidents.CATEGORY_RENDERER_CRASH, incidents.CATEGORY_BROWSER_PROCESS_CRASH}

    assert [r["instance_id"] for r in incidents.recent(category=both)] == ["r1", "p1", "r2"]
    assert [r["instance_id"] for r in incidents.recent(category=both, limit=2)] == ["p1", "r2"]
    assert [r["instance_id"] for r in incidents.recent(category=incidents.CATEGORY_RENDERER_CRASH)] == ["r1", "r2"]
    incidents.reset()


def test_a_process_crash_process_recovery_failure_says_relaunch() -> None:
    """The correction to recovering=True when reopening the crashed browser failed."""
    from octowright.browser_pool.events import SessionRecoveredEvent

    event = SessionRecoveredEvent("p1", "chromium", "lbl", None, "failed", 1, "/tmp/p1.jsonl", scope="process")
    params = notification_payload(event)["params"]

    assert params["scope"] == "process"
    assert params["outcome"] == "failed"
    assert "browser_launch" in params["hint"]
    assert "lost_sessions" in params["hint"]
    renderer = notification_payload(SessionRecoveredEvent("p1", "chromium", "lbl", None, "failed", 1, "/tmp/p1.jsonl"))
    assert renderer["params"]["scope"] == "renderer"
    assert renderer["params"]["hint"] != params["hint"]


def test_a_process_crash_degrades_health() -> None:
    from octowright.browser_pool import health

    verdict = health.assess(driver_restarts=0, recovery_failures=0, recovery_exhausted=0, process_crashes=2)

    assert verdict["status"] == "degraded"
    assert any("browser process" in reason for reason in verdict["reasons"])


def test_status_health_counts_retained_process_crashes() -> None:
    from octowright.server.meta import octowright_status

    incidents.reset()
    incidents.record(incidents.CATEGORY_BROWSER_PROCESS_CRASH, instance_id="p1", outcome="lost")

    health = octowright_status()["health"]

    assert health["status"] != "ok"
    assert any("browser process" in reason for reason in health["reasons"])
    incidents.reset()
