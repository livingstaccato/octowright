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

import json
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool import launch_helpers
from octowright.browser_pool.options import LaunchOptions
from octowright.request_errors import InvalidRequestError

# ─── to_pool_kwargs ──────────────────────────────────────────────────────────


class TestToPoolKwargs:
    def test_round_trip_all_fields_populated(self) -> None:
        """``to_pool_kwargs`` returns every caller-settable field as a kwarg.

        This docstring said "every field" while the golden dict below listed 27
        and omitted ``base_url`` -- so the test asserted the very defect it read
        as ruling out, and passed while ``LaunchOptions(base_url=...)
        .to_pool_kwargs()`` silently dropped it.

        ``to_pool_kwargs`` now derives its keys from ``CALLER_SETTABLE_FIELDS``,
        the same set ``from_mapping`` accepts, so a new field appears here
        without anyone editing it. The golden dict is kept anyway: derivation
        makes the two SETS agree, and this is what still catches a field being
        renamed or emitted with the wrong value. ``protected_reason`` is the one
        exclusion, being an output of ``resolve_protected`` rather than an input.
        """
        opts = LaunchOptions(
            kind="chromium",
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
            extra_http_headers_urls=["**/api/**"],
            disable_gpu=True,
            disable_automation_controlled=True,
            wayland_native=False,
            base_url="https://dev.test",
            trusted_launch_url="https://origin.test/",
        )
        assert opts.to_pool_kwargs() == {
            "kind": "chromium",
            "base_url": "https://dev.test",
            "trusted_launch_url": "https://origin.test/",
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
            "extra_http_headers_urls": ["**/api/**"],
            "disable_gpu": True,
            "disable_automation_controlled": True,
            "wayland_native": False,
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
        assert kwargs["disable_automation_controlled"] is False

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
    @pytest.mark.parametrize("value", ["false", 0, 1, None, [], {}])
    def test_disable_automation_controlled_rejects_non_boolean(self, value: object) -> None:
        with pytest.raises(InvalidRequestError, match="must be a boolean"):
            LaunchOptions.from_launch_record({"kind": "chromium", "disable_automation_controlled": value})

    def test_disable_automation_controlled_restores_true(self) -> None:
        opts = LaunchOptions.from_launch_record({"kind": "chromium", "disable_automation_controlled": True})
        assert opts.disable_automation_controlled is True

    def test_old_record_defaults_disable_automation_controlled_false(self) -> None:
        opts = LaunchOptions.from_launch_record({"kind": "chromium"})
        assert opts.disable_automation_controlled is False

    @pytest.mark.parametrize("value", [None, "false", "true", 0, 1, [], {}])
    def test_a_non_boolean_headed_is_refused_not_read_as_headless(self, value: object) -> None:
        # The writer always records the resolved bool, so anything else came from
        # a corrupt or poisoned file. Read loosely, a null meant auto-resolve
        # (headless on a display-less host) and "false" meant headed.
        with pytest.raises(InvalidRequestError, match="headed must be a boolean"):
            LaunchOptions.from_launch_record({"kind": "chromium", "headed": value})

    @pytest.mark.parametrize("value", [True, False])
    def test_a_boolean_headed_is_restored(self, value: bool) -> None:
        assert LaunchOptions.from_launch_record({"kind": "chromium", "headed": value}).headed is value

    def test_a_record_without_headed_keeps_the_historical_headed_default(self) -> None:
        assert LaunchOptions.from_launch_record({"kind": "chromium"}).headed is True

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


# ─── a real HAR-less launch record ───────────────────────────────────────────


def _har_less_launch_row(tmp_path: Path) -> dict[str, Any]:
    """The ``launch`` row the real writer emits for a launch without HAR.

    It writes ``har_mode``/``har_url_filter``/``har_content`` as explicit
    ``null`` -- which ``dict.get``'s default does not cover."""
    from octowright.recorder import Recorder

    log_path = tmp_path / "s.jsonl"
    recorder = Recorder(log_path)
    launch_helpers._record_launch_event(
        recorder,
        instance_id="i1",
        kind="chromium",
        label=None,
        profile=None,
        user_data_dir=None,
        target_url="https://x.test/",
        headless=True,
        log_viewport=None,
        stabilize=False,
        record_video=False,
        video_dir=None,
        trace=False,
        har_path=None,
        har_mode="minimal",
        har_url_filter=None,
        har_content=None,
        badge=True,
        badge_position="bottom-right",
        tile=False,
        ephemeral=False,
        session=False,
        disable_automation_controlled=False,
    )
    recorder.close()
    row: dict[str, Any] = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    return row


class TestHarLessLaunchRecord:
    def test_writer_records_har_fields_as_null(self, tmp_path: Path) -> None:
        row = _har_less_launch_row(tmp_path)
        assert row["har_mode"] is None
        assert row["har_url_filter"] is None
        assert row["har_content"] is None

    def test_round_trips_through_from_launch_record(self, tmp_path: Path) -> None:
        opts = LaunchOptions.from_launch_record(_har_less_launch_row(tmp_path))
        assert opts.har is False
        assert opts.har_mode == "minimal"
        assert opts.har_url_filter is None
        assert opts.har_content is None
        assert opts.headed is False
        assert opts.url == "https://x.test/"

    def test_http_relaunch_from_a_har_less_recording(self, tmp_path: Path) -> None:
        from octowright.http.routes.sessions_recording import _relaunch_kwargs_from_record

        kwargs = _relaunch_kwargs_from_record(_har_less_launch_row(tmp_path))
        assert kwargs["har_mode"] == "minimal"
        assert kwargs["har"] is False

    @pytest.mark.parametrize("value", ["junk", "", 0, False, [], {}])
    def test_a_non_null_junk_har_mode_is_still_refused(self, tmp_path: Path, value: object) -> None:
        row = _har_less_launch_row(tmp_path)
        row["har_mode"] = value
        with pytest.raises(InvalidRequestError, match="har_mode must be one of"):
            LaunchOptions.from_launch_record(row)


@pytest.mark.parametrize(
    ("field", "message"),
    [("badge_position", "badge_position must be one of"), ("har_content", "har_content must be one of")],
)
@pytest.mark.parametrize("value", [[], {}])
def test_an_unhashable_poisoned_value_is_refused_not_a_type_error(
    tmp_path: Path, field: str, message: str, value: object
) -> None:
    row = _har_less_launch_row(tmp_path)
    row[field] = value
    with pytest.raises(InvalidRequestError, match=message):
        LaunchOptions.from_launch_record(row)


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
