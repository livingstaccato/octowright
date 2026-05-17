# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os
import platform
from pathlib import Path


def user_config_dir(app_name: str = "octowright") -> Path:
    """Return this platform's per-user config directory for Octowright."""
    if platform.system() == "Windows":
        if appdata := os.environ.get("APPDATA"):
            return Path(appdata) / app_name
        return Path.home() / "AppData" / "Roaming" / app_name

    if xdg_config_home := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg_config_home) / app_name
    return Path.home() / ".config" / app_name


def user_state_dir(app_name: str = "octowright") -> Path:
    """Return this platform's per-user state directory for Octowright."""
    if platform.system() == "Windows":
        if localappdata := os.environ.get("LOCALAPPDATA"):
            return Path(localappdata) / app_name / "State"
        return Path.home() / "AppData" / "Local" / app_name / "State"

    if xdg_state_home := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg_state_home) / app_name
    return Path.home() / ".local" / "state" / app_name


def user_cache_dir(app_name: str = "octowright") -> Path:
    """Return this platform's per-user cache directory for Octowright."""
    if platform.system() == "Windows":
        if localappdata := os.environ.get("LOCALAPPDATA"):
            return Path(localappdata) / app_name / "Cache"
        return Path.home() / "AppData" / "Local" / app_name / "Cache"

    if xdg_cache_home := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg_cache_home) / app_name
    return Path.home() / ".cache" / app_name
