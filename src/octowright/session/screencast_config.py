# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os


def _int_from_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def screencast_fps() -> int:
    return max(1, _int_from_env("OCTOWRIGHT_LIVE_SCREENCAST_FPS", 10))


def screencast_quality() -> int:
    return min(100, max(1, _int_from_env("OCTOWRIGHT_LIVE_SCREENCAST_QUALITY", 70)))


def fullscreen_mode() -> str:
    value = os.environ.get("OCTOWRIGHT_LIVE_SCREENCAST_FULLSCREEN_MODE", "native").strip().lower()
    if value in {"native", "panel"}:
        return value
    return "native"


def screencast_config_block() -> dict[str, int | str]:
    return {
        "fps": screencast_fps(),
        "quality": screencast_quality(),
        "fullscreen_mode": fullscreen_mode(),
    }
