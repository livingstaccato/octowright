# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import TypedDict


class LaunchOptions(TypedDict, total=False):
    kind: str
    url: str | None
    headed: bool | None
    label: str | None
    viewport_w: int | None
    viewport_h: int | None
    profile: str | None
    stabilize: bool
    record_video: bool
    trace: bool
    badge: bool
    badge_position: str
    tile: bool
    ephemeral: bool
    session: bool
