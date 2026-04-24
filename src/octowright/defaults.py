# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_URL = os.environ.get("OCTOWRIGHT_DEFAULT_URL", "https://warp.undef.games")

DEFAULT_VIEWPORT_W = int(os.environ.get("OCTOWRIGHT_VIEWPORT_W", "1280"))
DEFAULT_VIEWPORT_H = int(os.environ.get("OCTOWRIGHT_VIEWPORT_H", "800"))

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_RECORDINGS = _REPO_ROOT / "recordings"
_DEFAULT_PROFILES = Path.home() / ".config" / "undef" / "profiles"
_DEFAULT_SCENARIOS = Path.home() / ".config" / "undef" / "scenarios"

RECORDINGS_DIR = Path(os.environ.get("OCTOWRIGHT_RECORDINGS", str(_DEFAULT_RECORDINGS)))
PROFILES_DIR = Path(os.environ.get("OCTOWRIGHT_PROFILES_DIR", str(_DEFAULT_PROFILES)))
SCENARIOS_DIR = Path(os.environ.get("OCTOWRIGHT_SCENARIOS_DIR", str(_DEFAULT_SCENARIOS)))

# Octowright defaults to HEADED mode. The whole point of this server is giving
# humans a window they can watch (and sometimes drive by hand), so headless is
# only correct when the caller has a specific background-verification reason —
# e.g. scripted health checks, parity runs, or CI. A caller wanting headless
# must pass headed=False explicitly on each browser_launch call. The env-var
# override below exists solely for unattended test harnesses.
HEADLESS_DEFAULT = os.environ.get("OCTOWRIGHT_HEADLESS", "0") == "1"

SUPPORTED_KINDS = ("chromium", "firefox", "webkit")

DEFAULT_NAV_TIMEOUT_MS = int(os.environ.get("OCTOWRIGHT_NAV_TIMEOUT_MS", "30000"))
DEFAULT_ACTION_TIMEOUT_MS = int(os.environ.get("OCTOWRIGHT_ACTION_TIMEOUT_MS", "15000"))
