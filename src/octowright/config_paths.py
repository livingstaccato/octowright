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
