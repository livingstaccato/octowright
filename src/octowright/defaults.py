# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os
import platform
from pathlib import Path

from octowright.config_paths import user_config_dir

DEFAULT_URL = os.environ.get("OCTOWRIGHT_DEFAULT_URL", "https://example.com")

DEFAULT_VIEWPORT_W = int(os.environ.get("OCTOWRIGHT_VIEWPORT_W", "1280"))
DEFAULT_VIEWPORT_H = int(os.environ.get("OCTOWRIGHT_VIEWPORT_H", "800"))

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = user_config_dir()
_DEFAULT_RECORDINGS = _REPO_ROOT / "recordings"
_DEFAULT_PROFILES = _CONFIG_DIR / "profiles"
_DEFAULT_SCENARIOS = _CONFIG_DIR / "scenarios"

RECORDINGS_DIR = Path(os.environ.get("OCTOWRIGHT_RECORDINGS", str(_DEFAULT_RECORDINGS)))
PROFILES_DIR = Path(os.environ.get("OCTOWRIGHT_PROFILES_DIR", str(_DEFAULT_PROFILES)))
SCENARIOS_DIR = Path(os.environ.get("OCTOWRIGHT_SCENARIOS_DIR", str(_DEFAULT_SCENARIOS)))
SCENARIO_TEMPLATES_DIR = SCENARIOS_DIR / "templates"
SESSION_MANIFEST_PATH = Path(
    os.environ.get("OCTOWRIGHT_SESSION_MANIFEST", str(RECORDINGS_DIR / "session-manifest.json"))
)


# Octowright defaults to HEADED mode. The whole point of this server is giving
# humans a window they can watch (and sometimes drive by hand), so headless is
# only correct when the caller has a specific background-verification reason —
# scripted health checks, parity runs, CI.
#
# Resolution order for the default headless value (each step short-circuits):
#   1. OCTOWRIGHT_HEADLESS=1 / =0 — explicit override always wins.
#   2. CI=true (set by GitHub Actions, GitLab, CircleCI, Travis, etc.) → headless.
#   3. Linux session with no $DISPLAY and no $WAYLAND_DISPLAY (SSH, no X server,
#      headless container) → headless.
#   4. Otherwise → headed.
def _detect_headless_default() -> bool:
    explicit = os.environ.get("OCTOWRIGHT_HEADLESS")
    if explicit is not None:
        return explicit == "1"
    if os.environ.get("CI", "").lower() in ("true", "1", "yes"):
        return True
    # macOS always has a window server; only Linux can be display-less here.
    return platform.system() == "Linux" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")


HEADLESS_DEFAULT = _detect_headless_default()

SUPPORTED_KINDS = ("chromium", "firefox", "webkit")

DEFAULT_NAV_TIMEOUT_MS = int(os.environ.get("OCTOWRIGHT_NAV_TIMEOUT_MS", "30000"))
DEFAULT_ACTION_TIMEOUT_MS = int(os.environ.get("OCTOWRIGHT_ACTION_TIMEOUT_MS", "15000"))

# Per-action delay applied to macros, useful for visually following execution.
# Sleep happens AFTER pushing status to the pill and BEFORE dispatching the
# action, so the pill reflects the upcoming action while the user gets time
# to see it. 0 disables. Override per-call via the `slowmo_ms` arg on
# run_macro / macro_run / macro_run_sequence.
MACRO_SLOWMO_MS = int(os.environ.get("OCTOWRIGHT_MACRO_SLOWMO_MS", "0"))

# HTTP debugger / dashboard sidecar — runs alongside the MCP stdio server when
# `octowright serve` is invoked. Bind defaults to localhost only because the
# debugger UI exposes raw recordings, video, and trace data.
HTTP_HOST = os.environ.get("OCTOWRIGHT_HTTP_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("OCTOWRIGHT_HTTP_PORT", "8765"))
# When the configured port is in use, try this many higher ports before giving up.
HTTP_PORT_RETRIES = 5
DASHBOARD_REMOTE_ALLOWED_ENV = "OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD"
NETWORK_EVENT_LIMIT = int(os.environ.get("OCTOWRIGHT_NETWORK_EVENT_LIMIT", "5000"))

# Idle-watchdog: once the pool sits empty for this many seconds, `octowright
# serve` exits on its own. Override with --idle-grace or --keep-alive to disable.
# The poll interval below controls how often the watchdog samples the pool —
# keep it short so shutdown is snappy.
#
# Default raised from 30s to 300s (5 min) so a daemon that's been spawned but
# is waiting on the first browser_launch survives normal chat-paced workflows
# (talking with the MCP client, exploring docs, etc.). Truly unused daemons
# still self-clean within minutes; not hours.
IDLE_GRACE_SECONDS = float(os.environ.get("OCTOWRIGHT_IDLE_GRACE", "300"))
IDLE_POLL_SECONDS = float(os.environ.get("OCTOWRIGHT_IDLE_POLL", "2"))
