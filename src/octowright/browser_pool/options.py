# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from octowright.browser_pool.visuals import _BADGE_POSITION_DEFAULT, _BADGE_POSITIONS
from octowright.defaults import SUPPORTED_KINDS, get_default_url


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
    protected: bool = False

    @classmethod
    def from_mapping(cls, options: dict[str, Any]) -> LaunchOptions:
        from octowright.defaults import PROTECT_BROWSERS_DEFAULT

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
            protected=options.get("protected", PROTECT_BROWSERS_DEFAULT),
        )
        launch_options.validate()
        return launch_options

    @classmethod
    def from_launch_record(cls, record: dict[str, Any]) -> LaunchOptions:
        """Translate a JSONL ``launch`` event back into ``LaunchOptions``.

        Unlike ``from_mapping`` (flat dict matching the kwarg names), the
        recording shape uses a nested ``viewport`` dict and stores video
        capture as a ``video_dir`` path rather than a ``record_video`` bool.
        Pre-this-schema recordings also lack the explicit ``headed`` field;
        the historical default for those is ``True``. HAR rotation is the
        caller's job — chain ``.with_har_rotated()`` if you want it.

        Treats every JSONL field as untrusted: a malicious actor able to
        drop a crafted ``*.jsonl`` into ``RECORDINGS_DIR`` (other local user,
        poisoned CI step, etc.) could otherwise pick the engine, profile, or
        HAR write-target on relaunch. ``validate()`` rejects unknown
        ``kind``/``badge_position``/``har_mode``/``har_content`` values, and
        the ``har_path`` containment check below blocks write-anywhere.
        """
        viewport = record.get("viewport") if isinstance(record.get("viewport"), dict) else None
        har_path = record.get("har_path")
        if har_path is not None:
            # HAR writes go under RECORDINGS_DIR by construction in the live
            # launch path. Enforce that on the JSONL replay path too, so a
            # poisoned record can't redirect HAR writes anywhere on disk.
            # Read defaults.RECORDINGS_DIR dynamically so tests that
            # monkeypatch it (or reload defaults after setenv) see the
            # current value, not the import-time snapshot.
            from octowright import defaults as _defaults
            from octowright._paths import safe_under

            if not safe_under(Path(har_path), _defaults.RECORDINGS_DIR):
                har_path = None
        return cls.from_mapping(
            {
                "kind": record.get("kind", "chromium"),
                "url": record.get("url") or get_default_url(),
                "label": record.get("label"),
                "profile": record.get("profile"),
                "viewport_w": viewport.get("w") if viewport else None,
                "viewport_h": viewport.get("h") if viewport else None,
                "headed": record.get("headed", True),
                "stabilize": record.get("stabilize", False),
                "record_video": bool(record.get("video_dir")),
                "trace": record.get("trace", False),
                "har": bool(record.get("har")) and har_path is not None,
                "har_path": har_path,
                "har_mode": record.get("har_mode", "minimal"),
                "har_url_filter": record.get("har_url_filter"),
                "har_content": record.get("har_content"),
                "badge": record.get("badge", True),
                "badge_position": record.get("badge_position", _BADGE_POSITION_DEFAULT),
                "tile": record.get("tile", False),
                "ephemeral": record.get("ephemeral", False),
                "session": record.get("session", False),
            }
        )

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

    def to_pool_kwargs(self) -> dict[str, Any]:
        """Flatten back to the kwarg dict accepted by ``BrowserPool.launch``.

        This is the canonical shape every call site (``browser_launch``,
        ``browser_quick_launch``, HTTP ``session_launch``, JSONL relaunch)
        funnels through, so adding a new field is a one-line edit here +
        one new dataclass attribute above.
        """
        return {
            "kind": self.kind,
            "url": self.url,
            "headed": self.headed,
            "label": self.label,
            "viewport_w": self.viewport_w,
            "viewport_h": self.viewport_h,
            "profile": self.profile,
            "stabilize": self.stabilize,
            "record_video": self.record_video,
            "trace": self.trace,
            "har": self.har,
            "har_path": self.har_path,
            "har_mode": self.har_mode,
            "har_url_filter": self.har_url_filter,
            "har_content": self.har_content,
            "badge": self.badge,
            "badge_position": self.badge_position,
            "tile": self.tile,
            "ephemeral": self.ephemeral,
            "session": self.session,
            "protected": self.protected,
        }

    def with_har_rotated(self) -> LaunchOptions:
        """Return a copy whose ``har_path`` won't clobber an existing recording.

        No-op when ``har_path`` is unset. When set, picks the next free
        sibling path via ``rotate_har_path`` and re-flags ``har=True`` so
        relaunch/handoff callers that copy a session's HAR config don't
        have to also remember to set ``har``.
        """
        if not self.har_path:
            return self
        # Local import to avoid a launch_helpers → options circular import.
        from octowright.browser_pool.launch_helpers import rotate_har_path

        rotated = rotate_har_path(Path(self.har_path))
        return replace(self, har=True, har_path=str(rotated) if rotated else None)
