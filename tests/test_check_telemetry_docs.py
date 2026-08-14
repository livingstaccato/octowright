# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The telemetry/notification doc ratchet (scripts/check_telemetry_docs.py).

It must (a) extract every emitted metric + notification from src, (b) flag drift
when something isn't documented, and (c) be clean against the real AGENTS.md — so
a new metric/notification can't ship undocumented."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_telemetry_docs", _ROOT / "scripts" / "check_telemetry_docs.py")
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def test_extracts_the_new_stability_metrics() -> None:
    names = checker.metric_names()
    assert {
        "octowright_driver_restart_total",
        "octowright_driver_lost_total",
        "octowright_launch_refused_total",
        "octowright_orphan_reaped_total",
        "octowright_bridge_leader_recovery_total",
        "octowright_process_rss_bytes",
    } <= names


def test_extracts_operation_gate_metrics() -> None:
    assert {
        "octowright_operation_queue_wait_seconds",
        "octowright_operation_active_duration_seconds",
        "octowright_operation_queue_timeout_total",
        "octowright_operation_rejected_total",
        "octowright_operation_queue_depth",
    } <= checker.metric_names()


def test_extracts_all_notification_methods() -> None:
    assert {"browser_crashed", "browser_recovered", "driver_died", "session_closed"} <= checker.notification_methods()


def test_empty_doc_flags_everything() -> None:
    # The make-break half: an empty doc must surface every metric + notification.
    missing_metrics, missing_notifs = checker.undocumented("")
    assert "octowright_driver_restart_total" in missing_metrics
    assert "browser_recovered" in missing_notifs
    assert len(missing_metrics) == len(checker.metric_names())
    assert len(missing_notifs) == len(checker.notification_methods())


def test_current_agents_md_is_in_sync() -> None:
    # The catch half: the real AGENTS.md documents everything emitted today.
    doc = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    missing_metrics, missing_notifs = checker.undocumented(doc)
    assert missing_metrics == [], f"undocumented metrics: {missing_metrics}"
    assert missing_notifs == [], f"undocumented notifications: {missing_notifs}"
