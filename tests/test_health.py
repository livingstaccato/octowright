# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Computed health verdict (browser_pool.health) surfaced in octowright_status."""

from __future__ import annotations

from octowright.browser_pool import health


def test_healthy_when_nothing_wrong() -> None:
    v = health.assess(driver_restarts=0, recovery_failures=0, recovery_exhausted=0)
    assert v["status"] == "ok"
    assert v["reasons"] == []


def test_degraded_on_single_driver_restart() -> None:
    v = health.assess(driver_restarts=1, recovery_failures=0, recovery_exhausted=0)
    assert v["status"] == "degraded"
    assert any("driver" in r for r in v["reasons"])


def test_degraded_on_recovery_failures() -> None:
    v = health.assess(driver_restarts=0, recovery_failures=2, recovery_exhausted=0)
    assert v["status"] == "degraded"
    assert any("recover" in r.lower() for r in v["reasons"])


def test_degraded_on_crash_loop_exhaustion() -> None:
    v = health.assess(driver_restarts=0, recovery_failures=0, recovery_exhausted=1)
    assert v["status"] == "degraded"
    assert any("loop" in r.lower() or "cap" in r.lower() for r in v["reasons"])


def test_critical_when_driver_restarts_pass_threshold() -> None:
    v = health.assess(driver_restarts=health.CRITICAL_DRIVER_RESTARTS, recovery_failures=0, recovery_exhausted=0)
    assert v["status"] == "critical"


def test_critical_when_recovery_failures_pass_threshold() -> None:
    v = health.assess(driver_restarts=0, recovery_failures=health.CRITICAL_RECOVERY_FAILURES, recovery_exhausted=0)
    assert v["status"] == "critical"


def test_reasons_accumulate_across_signals() -> None:
    v = health.assess(driver_restarts=1, recovery_failures=1, recovery_exhausted=1)
    assert len(v["reasons"]) == 3
