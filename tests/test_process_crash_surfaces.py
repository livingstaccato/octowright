# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Where a browser-process crash is visible: push, pull, and crash-report correlation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from octowright.browser_pool import crash_reports, incidents
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


def test_a_process_crash_is_correlated_with_its_macos_crash_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real crash writes a ``.ips`` like a renderer crash does."""
    from datetime import datetime

    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    ts = "2026-06-26T19:00:00.000Z"
    report = tmp_path / "Chromium-2026-06-26-190001.ips"
    report.write_text('{"bug_type":"309"}\n{"exception":{"signal":"SIGSEGV","type":"EXC_BAD_ACCESS"}}\n')
    when = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() + 1.0
    os.utime(report, (when, when))

    out = crash_reports.enrich([{"category": incidents.CATEGORY_BROWSER_PROCESS_CRASH, "ts": ts}], reports_dir=tmp_path)

    assert out[0]["crash_report"]["signal"] == "SIGSEGV"


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
