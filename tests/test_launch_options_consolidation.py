# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""LaunchOptions consolidation: ``to_pool_kwargs``, ``from_launch_record``,
``with_har_rotated``.

These tests pin the canonical kwargs shape that all four launch call sites
funnel through (``browser_launch``, ``browser_quick_launch``, HTTP
``session_launch``, and the JSONL ``_relaunch_kwargs_from_record``
translator). Adding a new launch field should require updating one place
here, not editing four call sites in parallel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.browser_pool import launch_helpers
from octowright.browser_pool.options import LaunchOptions

# ─── to_pool_kwargs ──────────────────────────────────────────────────────────


class TestToPoolKwargs:
    def test_round_trip_all_fields_populated(self) -> None:
        """``to_pool_kwargs`` returns every LaunchOptions field as a kwarg.

        A new field is the only thing that should break this test — if you
        added one to LaunchOptions, add it to to_pool_kwargs and update the
        golden dict below.
        """
        opts = LaunchOptions(
            kind="firefox",
            url="https://x.test",
            headed=False,
            label="lab",
            viewport_w=1024,
            viewport_h=768,
            profile="cosmo",
            stabilize=True,
            record_video=True,
            trace=True,
            har=True,
            har_path="captures/a.har",
            har_mode="full",
            har_url_filter="octowright.com",
            har_content="embed",
            badge=False,
            badge_position="top-left",
            tile=True,
            ephemeral=False,
            session=True,
            protected=True,
            channel="chrome",
            executable_path="/opt/chrome/chrome",
            launch_args=["--foo"],
            extra_http_headers={"X-Env": "staging"},
        )
        assert opts.to_pool_kwargs() == {
            "kind": "firefox",
            "url": "https://x.test",
            "headed": False,
            "label": "lab",
            "viewport_w": 1024,
            "viewport_h": 768,
            "profile": "cosmo",
            "stabilize": True,
            "record_video": True,
            "trace": True,
            "har": True,
            "har_path": "captures/a.har",
            "har_mode": "full",
            "har_url_filter": "octowright.com",
            "har_content": "embed",
            "badge": False,
            "badge_position": "top-left",
            "tile": True,
            "ephemeral": False,
            "session": True,
            "protected": True,
            "channel": "chrome",
            "executable_path": "/opt/chrome/chrome",
            "launch_args": ["--foo"],
            "extra_http_headers": {"X-Env": "staging"},
        }

    def test_defaults(self) -> None:
        """Bare ``LaunchOptions()`` produces the default kwargs."""
        kwargs = LaunchOptions().to_pool_kwargs()
        assert kwargs["kind"] == "chromium"
        assert kwargs["url"] is None
        assert kwargs["headed"] is None
        assert kwargs["stabilize"] is False
        assert kwargs["har"] is False
        assert kwargs["har_path"] is None
        assert kwargs["har_mode"] == "minimal"
        assert kwargs["badge"] is True
        assert kwargs["badge_position"] == "bottom-right"
        assert kwargs["tile"] is False
        assert kwargs["ephemeral"] is False
        assert kwargs["session"] is False

    def test_from_mapping_round_trip(self) -> None:
        """``from_mapping(d).to_pool_kwargs()`` reproduces the input keys."""
        src = {
            "kind": "webkit",
            "url": "https://y.test",
            "label": "abc",
            "viewport_w": 800,
            "viewport_h": 600,
        }
        out = LaunchOptions.from_mapping(src).to_pool_kwargs()
        for key, val in src.items():
            assert out[key] == val


# ─── from_launch_record ──────────────────────────────────────────────────────


class TestFromLaunchRecord:
    def test_viewport_dict_unpacks_to_w_h(self) -> None:
        record = {
            "kind": "chromium",
            "url": "https://x.test",
            "viewport": {"w": 1440, "h": 900},
        }
        opts = LaunchOptions.from_launch_record(record)
        assert opts.viewport_w == 1440
        assert opts.viewport_h == 900

    def test_viewport_missing_yields_none(self) -> None:
        record = {"kind": "chromium", "url": "https://x.test", "viewport": None}
        opts = LaunchOptions.from_launch_record(record)
        assert opts.viewport_w is None
        assert opts.viewport_h is None

    def test_viewport_non_dict_falls_back_to_none(self) -> None:
        record = {"kind": "chromium", "url": "https://x.test", "viewport": "1280x800"}
        opts = LaunchOptions.from_launch_record(record)
        assert opts.viewport_w is None
        assert opts.viewport_h is None

    def test_video_dir_promotes_to_record_video_true(self) -> None:
        record = {"kind": "chromium", "url": "https://x.test", "video_dir": "/tmp/v"}
        opts = LaunchOptions.from_launch_record(record)
        assert opts.record_video is True

    def test_no_video_dir_means_record_video_false(self) -> None:
        record = {"kind": "chromium", "url": "https://x.test", "video_dir": None}
        opts = LaunchOptions.from_launch_record(record)
        assert opts.record_video is False

    def test_default_headed_true_when_absent(self) -> None:
        """Recordings predate the explicit ``headed`` field; default to True."""
        record = {"kind": "chromium", "url": "https://x.test"}
        opts = LaunchOptions.from_launch_record(record)
        assert opts.headed is True

    def test_default_url_when_absent_or_falsy(self) -> None:
        from octowright.defaults import DEFAULT_URL

        opts_empty = LaunchOptions.from_launch_record({"kind": "chromium"})
        assert opts_empty.url == DEFAULT_URL
        opts_blank = LaunchOptions.from_launch_record({"kind": "chromium", "url": ""})
        assert opts_blank.url == DEFAULT_URL


# ─── with_har_rotated ────────────────────────────────────────────────────────


class TestWithHarRotated:
    def test_no_har_path_is_noop(self) -> None:
        opts = LaunchOptions(har=False, har_path=None)
        rotated = opts.with_har_rotated()
        assert rotated is opts

    def test_har_path_not_on_disk_keeps_path(self, tmp_path: Path) -> None:
        target = tmp_path / "nope.har"
        opts = LaunchOptions(har=True, har_path=str(target))
        rotated = opts.with_har_rotated()
        # File does not exist, so next_har_path returns the same path; the
        # rotated copy must reflect that.
        assert rotated.har_path == str(target)
        assert rotated.har is True

    def test_existing_har_path_rotates_to_sibling(self, tmp_path: Path) -> None:
        target = tmp_path / "demo.har"
        target.write_text("prior HAR", encoding="utf-8")
        opts = LaunchOptions(har=True, har_path=str(target))
        rotated = opts.with_har_rotated()
        assert rotated.har_path == str(tmp_path / "demo.1.har")
        assert rotated.har is True


# ─── rotate_har_path helper (Path | None form) ───────────────────────────────


class TestRotateHarPathHelper:
    def test_none_returns_none(self) -> None:
        assert launch_helpers.rotate_har_path(None) is None

    def test_missing_file_passes_through(self, tmp_path: Path) -> None:
        target = tmp_path / "fresh.har"
        assert launch_helpers.rotate_har_path(target) == target

    def test_existing_file_returns_sibling(self, tmp_path: Path) -> None:
        target = tmp_path / "rec.har"
        target.write_text("prior", encoding="utf-8")
        assert launch_helpers.rotate_har_path(target) == tmp_path / "rec.1.har"


# ─── next_har_path edge cases (focused unit coverage) ───────────────────────


class TestNextHarPath:
    def test_returns_same_path_when_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "a.har"
        assert launch_helpers.next_har_path(target) == target

    def test_rotates_until_free(self, tmp_path: Path) -> None:
        (tmp_path / "a.har").write_text("0", encoding="utf-8")
        (tmp_path / "a.1.har").write_text("1", encoding="utf-8")
        (tmp_path / "a.2.har").write_text("2", encoding="utf-8")
        assert launch_helpers.next_har_path(tmp_path / "a.har") == tmp_path / "a.3.har"

    def test_raises_when_rotations_exhausted(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(launch_helpers, "_MAX_HAR_ROTATIONS", 2)
        target = tmp_path / "b.har"
        target.write_text("0", encoding="utf-8")
        (tmp_path / "b.1.har").write_text("1", encoding="utf-8")
        (tmp_path / "b.2.har").write_text("2", encoding="utf-8")
        with pytest.raises(RuntimeError, match="exhausted 2 HAR rotations"):
            launch_helpers.next_har_path(target)
