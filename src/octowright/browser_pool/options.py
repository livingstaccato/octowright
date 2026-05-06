# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octowright.browser_pool.visuals import _BADGE_POSITION_DEFAULT, _BADGE_POSITIONS
from octowright.defaults import SUPPORTED_KINDS


@dataclass(frozen=True)
class LaunchOptions:
    kind: str = "chromium"
    url: str | None = None
    headed: bool | None = None
    label: str | None = None
    viewport_w: int | None = None
    viewport_h: int | None = None
    profile: str | None = None
    stabilize: bool = False
    record_video: bool = False
    trace: bool = False
    har: bool = False
    har_path: str | None = None
    har_mode: str = "minimal"
    har_url_filter: str | None = None
    har_content: str | None = None
    badge: bool = True
    badge_position: str = _BADGE_POSITION_DEFAULT
    tile: bool = False
    ephemeral: bool = False
    session: bool = False

    @classmethod
    def from_mapping(cls, options: dict[str, Any]) -> LaunchOptions:
        launch_options = cls(
            kind=options.get("kind", "chromium"),
            url=options.get("url"),
            headed=options.get("headed"),
            label=options.get("label"),
            viewport_w=options.get("viewport_w"),
            viewport_h=options.get("viewport_h"),
            profile=options.get("profile"),
            stabilize=options.get("stabilize", False),
            record_video=options.get("record_video", False),
            trace=options.get("trace", False),
            har=options.get("har", False),
            har_path=options.get("har_path"),
            har_mode=options.get("har_mode", "minimal"),
            har_url_filter=options.get("har_url_filter"),
            har_content=options.get("har_content"),
            badge=options.get("badge", True),
            badge_position=options.get("badge_position", _BADGE_POSITION_DEFAULT),
            tile=options.get("tile", False),
            ephemeral=options.get("ephemeral", False),
            session=options.get("session", False),
        )
        launch_options.validate()
        return launch_options

    def validate(self) -> None:
        if self.kind not in SUPPORTED_KINDS:
            raise ValueError(f"kind must be one of {SUPPORTED_KINDS}, got {self.kind!r}")
        if self.badge_position not in _BADGE_POSITIONS:
            raise ValueError(f"badge_position must be one of {sorted(_BADGE_POSITIONS)}, got {self.badge_position!r}")
        if self.ephemeral and self.session:
            raise ValueError("ephemeral and session are mutually exclusive")
        if self.profile and self.session:
            raise ValueError("profile and session are mutually exclusive")
        if self.har_mode not in {"full", "minimal"}:
            raise ValueError("har_mode must be one of ['full', 'minimal']")
        if self.har_content is not None and self.har_content not in {"omit", "embed", "attach"}:
            raise ValueError("har_content must be one of ['omit', 'embed', 'attach']")

    def promoted_profile(self) -> str | None:
        if self.profile is None and self.label is not None and not self.ephemeral and not self.session:
            return self.label
        return self.profile

    def session_name(self, instance_id: str) -> str:
        return self.label or instance_id
