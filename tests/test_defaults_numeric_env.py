# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A malformed numeric knob falls back to its default instead of crashing.

About forty numeric ``OCTOWRIGHT_*`` knobs were parsed with a bare ``int()`` or
``float()`` at import, so one typo (``OCTOWRIGHT_NAV_TIMEOUT_MS=30s``) raised
``ValueError`` from every command -- even the stdio follower, which has no use
for the value, failed to start and the MCP client saw a dead server.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from octowright import defaults

# Every module that read a numeric knob at import with a bare int()/float().
_MODULES = (
    "octowright.defaults",
    "octowright.proxy_runtime",
    "octowright.proxy_supervisor",
    "octowright.server._heartbeat",
    "octowright.browser_pool.health",
    "octowright.browser_pool.incidents",
    "octowright.browser_pool.driver_relaunch",
)

_KNOBS = {
    "OCTOWRIGHT_NAV_TIMEOUT_MS": ("octowright.defaults", "DEFAULT_NAV_TIMEOUT_MS", 30000),
    "OCTOWRIGHT_BRIDGE_REQUEST_TIMEOUT_SECONDS": ("octowright.defaults", "BRIDGE_REQUEST_TIMEOUT_SECONDS", 20.0),
    "OCTOWRIGHT_CAPTURE_MAX_TOTAL_BYTES": ("octowright.defaults", "CAPTURE_MAX_TOTAL_BYTES", 50 * 1024 * 1024),
    "OCTOWRIGHT_HTTP_PORT": ("octowright.defaults", "HTTP_PORT", 6286),
    "OCTOWRIGHT_BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS": (
        "octowright.proxy_runtime",
        "BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS",
        180.0,
    ),
    "OCTOWRIGHT_BRIDGE_SUSPEND_THRESHOLD_SECONDS": ("octowright.proxy_supervisor", "SUSPEND_THRESHOLD_SECONDS", 5.0),
    "OCTOWRIGHT_HEARTBEAT_INTERVAL_SECONDS": ("octowright.server._heartbeat", "HEARTBEAT_INTERVAL_SECONDS", 8.0),
    "OCTOWRIGHT_HEALTH_CRITICAL_DRIVER_RESTARTS": ("octowright.browser_pool.health", "CRITICAL_DRIVER_RESTARTS", 3),
    "OCTOWRIGHT_INCIDENT_RING_SIZE": ("octowright.browser_pool.incidents", "_RING_SIZE", 25),
    "OCTOWRIGHT_LOST_SESSION_RING_SIZE": ("octowright.browser_pool.driver_relaunch", "_LOST_SIZE", 25),
}


def test_malformed_numeric_knobs_do_not_break_import() -> None:
    """In a child interpreter, so no module here is reloaded under a bad env."""
    env = dict(os.environ)
    for name in _KNOBS:
        env[name] = "30s"
    script = (
        "import importlib, json, sys\n"
        f"knobs = {json.dumps({k: [m, a] for k, (m, a, _d) in _KNOBS.items()})}\n"
        f"for mod in {list(_MODULES)!r}: importlib.import_module(mod)\n"
        "print(json.dumps({k: getattr(sys.modules[m], a) for k, (m, a) in knobs.items()}))\n"
    )
    out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    values = json.loads(out.stdout.strip().splitlines()[-1])
    for name, (_mod, _attr, default) in _KNOBS.items():
        assert values[name] == default, name


@pytest.mark.parametrize("raw", ["30s", "", "  ", "1e3x", "ten"])
def test_env_int_falls_back_on_garbage(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("OCTOWRIGHT_TEST_KNOB", raw)
    assert defaults.env_int("OCTOWRIGHT_TEST_KNOB", 7) == 7


def test_env_int_and_float_read_good_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_TEST_KNOB", " 42 ")
    assert defaults.env_int("OCTOWRIGHT_TEST_KNOB", 7) == 42
    monkeypatch.setenv("OCTOWRIGHT_TEST_KNOB", "2.5")
    assert defaults.env_float("OCTOWRIGHT_TEST_KNOB", 1.0) == 2.5
    monkeypatch.delenv("OCTOWRIGHT_TEST_KNOB")
    assert defaults.env_float("OCTOWRIGHT_TEST_KNOB", 1.5) == 1.5


def test_env_float_falls_back_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_TEST_KNOB", "20 seconds")
    assert defaults.env_float("OCTOWRIGHT_TEST_KNOB", 20.0) == 20.0
