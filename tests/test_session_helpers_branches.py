# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for the small low-coverage session helper modules.

Covers:
- session/downloads.py: _timestamp, save_download (happy + failure paths +
  pending-event signaling), wait_for_download_impl (immediate return, async
  wait + completion, timeout cleanup).
- session/frames.py: switch_frame_impl (selector / name / url_pattern,
  multi-arg + missing-arg ValueError, owner-callable branch, missing-frame
  RuntimeError), list_frames_impl (active-frame marker).
- session/locators.py: build_locator (each finder kind, role with/without
  role_name, role_exact passthrough, validation errors).
- stabilize.py: render_stabilize_script content + idempotence (already has
  some coverage, this fills the remaining mutations).
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session.downloads import _timestamp, save_download, wait_for_download_impl
from octowright.session.frames import list_frames_impl, switch_frame_impl
from octowright.session.locators import build_locator
from octowright.stabilize import STABILIZE_SCRIPT, render_stabilize_script


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── session/downloads: _timestamp ─────────────────────────────────────────


class TestTimestamp:
    def test_format_is_compact_utc(self) -> None:
        """_timestamp returns YYYYMMDDTHHMMSSZ — no separators."""
        ts = _timestamp()
        assert re.fullmatch(r"\d{8}T\d{6}Z", ts), f"unexpected shape: {ts!r}"

    def test_ends_with_z(self) -> None:
        """Trailing 'Z' indicates UTC."""
        assert _timestamp().endswith("Z")

    def test_is_recent(self) -> None:
        """Timestamp falls within ±5s of now."""
        now = datetime.now(UTC)
        ts = _timestamp()
        # Parse back and compare.
        parsed = datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        delta = abs((now - parsed).total_seconds())
        assert delta < 5


# ─── session/downloads: save_download ──────────────────────────────────────


def _fake_session(tmp_path: Any) -> Any:
    """Build a minimal session-shaped object for download tests."""
    sess = SimpleNamespace()
    sess.instance_id = "inst123"
    # Downloads anchor on the session's recordings root (log_path.parent), which
    # for a real session == the owning pool's recordings_dir. Point it at tmp_path
    # so it matches the monkeypatched RECORDINGS_DIR these tests assert against.
    sess.log_path = tmp_path / "session.jsonl"
    sess.downloads = []
    sess.download_count = 0
    sess.recorder = MagicMock()
    sess.recorder.record = MagicMock()
    sess._pending_download_events = []
    return sess


class TestSaveDownload:
    @pytest.mark.anyio
    async def test_writes_record_with_required_fields(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Record dict has url/suggested_filename/path/timestamp."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "RECORDINGS_DIR", tmp_path)
        sess = _fake_session(tmp_path)
        download = MagicMock()
        download.suggested_filename = "report.pdf"
        download.url = "https://x/report.pdf"
        download.save_as = AsyncMock()
        result = await save_download(sess, download)
        assert set(result.keys()) == {"url", "suggested_filename", "path", "timestamp"}
        assert result["url"] == "https://x/report.pdf"
        assert result["suggested_filename"] == "report.pdf"
        assert result["path"].endswith("000-report.pdf")

    @pytest.mark.anyio
    async def test_path_includes_zero_padded_index(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Filename gets a 000/001/002 prefix per existing downloads count."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "RECORDINGS_DIR", tmp_path)
        sess = _fake_session(tmp_path)
        # Pre-populate with two prior downloads.
        sess.downloads = [{"x": 1}, {"x": 2}]
        download = MagicMock()
        download.suggested_filename = "doc.pdf"
        download.url = "https://x/doc.pdf"
        download.save_as = AsyncMock()
        result = await save_download(sess, download)
        assert result["path"].endswith("002-doc.pdf")

    @pytest.mark.anyio
    async def test_creates_target_dir(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """RECORDINGS_DIR/downloads/<instance_id>/ is created."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "RECORDINGS_DIR", tmp_path)
        sess = _fake_session(tmp_path)
        download = MagicMock()
        download.suggested_filename = "x.txt"
        download.url = "https://x/"
        download.save_as = AsyncMock()
        await save_download(sess, download)
        target_dir = tmp_path / "downloads" / "inst123"
        assert target_dir.exists()

    @pytest.mark.anyio
    async def test_appends_to_session_downloads(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful download appends to session.downloads list."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "RECORDINGS_DIR", tmp_path)
        sess = _fake_session(tmp_path)
        download = MagicMock()
        download.suggested_filename = "x.txt"
        download.url = "https://x/"
        download.save_as = AsyncMock()
        await save_download(sess, download)
        assert len(sess.downloads) == 1
        assert sess.download_count == 1

    @pytest.mark.anyio
    async def test_records_download_saved_event(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recorder.record called with action='download_saved' and full kwargs."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "RECORDINGS_DIR", tmp_path)
        sess = _fake_session(tmp_path)
        download = MagicMock()
        download.suggested_filename = "x.txt"
        download.url = "https://x/"
        download.save_as = AsyncMock()
        await save_download(sess, download)
        sess.recorder.record.assert_called_once()
        call = sess.recorder.record.call_args
        assert call.args[0] == "download_saved"
        assert call.kwargs["url"] == "https://x/"
        assert call.kwargs["suggested_filename"] == "x.txt"

    @pytest.mark.anyio
    async def test_signals_pending_waiters(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each pending-event Event.set() is called and the list cleared."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "RECORDINGS_DIR", tmp_path)
        sess = _fake_session(tmp_path)
        ev1 = asyncio.Event()
        ev2 = asyncio.Event()
        sess._pending_download_events = [ev1, ev2]
        download = MagicMock()
        download.suggested_filename = "x.txt"
        download.url = "https://x/"
        download.save_as = AsyncMock()
        await save_download(sess, download)
        assert ev1.is_set()
        assert ev2.is_set()
        assert sess._pending_download_events == []

    @pytest.mark.anyio
    async def test_save_failure_records_error_event(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """save_as raising → recorder.record('download_save_error', ...) and {} returned."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "RECORDINGS_DIR", tmp_path)
        sess = _fake_session(tmp_path)
        download = MagicMock()
        download.suggested_filename = "x.txt"
        download.url = "https://x/"
        download.save_as = AsyncMock(side_effect=RuntimeError("boom"))
        result = await save_download(sess, download)
        assert result == {}
        # Recorder got download_save_error
        sess.recorder.record.assert_called_once()
        assert sess.recorder.record.call_args.args[0] == "download_save_error"
        assert "boom" in sess.recorder.record.call_args.kwargs["error"]
        # No record appended to downloads list
        assert sess.downloads == []
        assert sess.download_count == 0


# ─── session/downloads: wait_for_download_impl ─────────────────────────────


class TestWaitForDownload:
    @pytest.mark.anyio
    async def test_waits_for_next_download_even_with_prior_entries(self, tmp_path: Any) -> None:
        """Contract: blocks until the NEXT download fires, ignoring prior entries.
        Returning the last existing download immediately would race with the
        action that's supposed to trigger this wait."""
        sess = _fake_session(tmp_path)
        sess.downloads = [{"a": 1}, {"a": 2}]

        async def trigger() -> None:
            await asyncio.sleep(0.01)
            sess.downloads.append({"a": 3})
            for ev in sess._pending_download_events:
                ev.set()

        trigger_task = asyncio.create_task(trigger())
        result = await wait_for_download_impl(sess, timeout_ms=1000)
        await trigger_task
        assert result == {"a": 3}

    @pytest.mark.anyio
    async def test_waits_then_returns_after_event_set(self, tmp_path: Any) -> None:
        """If no download yet, waits on a pending-event; returns after set()."""
        sess = _fake_session(tmp_path)

        async def trigger() -> None:
            await asyncio.sleep(0.01)
            sess.downloads.append({"new": True})
            for ev in sess._pending_download_events:
                ev.set()

        # Start trigger concurrently with the waiter.
        trigger_task = asyncio.create_task(trigger())
        result = await wait_for_download_impl(sess, timeout_ms=1000)
        await trigger_task
        assert result == {"new": True}

    @pytest.mark.anyio
    async def test_timeout_raises_timeout_error_with_ms_message(self, tmp_path: Any) -> None:
        """No download arrives → TimeoutError with 'no download within Nms'."""
        sess = _fake_session(tmp_path)
        with pytest.raises(TimeoutError, match=r"no download within 50ms"):
            await wait_for_download_impl(sess, timeout_ms=50)

    @pytest.mark.anyio
    async def test_timeout_cleans_pending_event_list(self, tmp_path: Any) -> None:
        """After timeout, the orphaned event is removed from the pending list."""
        sess = _fake_session(tmp_path)
        with pytest.raises(TimeoutError):
            await wait_for_download_impl(sess, timeout_ms=50)
        assert sess._pending_download_events == []


# ─── session/frames: switch_frame_impl ─────────────────────────────────────


def _make_page_with_frames(num_frames: int = 3) -> Any:
    """Build a Page mock with `frames` attribute and frame_locator/frame methods."""
    page = MagicMock()
    frames = []
    for i in range(num_frames):
        f = MagicMock()
        f.url = f"https://octowright.com/frame-{i}"
        f.name = f"frame-{i}"
        frames.append(f)
    page.frames = frames
    return page, frames


class TestSwitchFrameValidation:
    @pytest.mark.anyio
    async def test_no_args_raises(self) -> None:
        """All three None → ValueError listing the empty 'provided' list."""
        page, _ = _make_page_with_frames()
        with pytest.raises(ValueError, match=r"exactly one of selector/name/url_pattern must be set"):
            await switch_frame_impl(page, selector=None, name=None, url_pattern=None)

    @pytest.mark.anyio
    async def test_two_args_raises(self) -> None:
        """Multiple args → ValueError listing them."""
        page, _ = _make_page_with_frames()
        with pytest.raises(ValueError, match=r"selector"):
            await switch_frame_impl(page, selector="iframe", name="foo", url_pattern=None)


class TestSwitchFrameSelector:
    @pytest.mark.anyio
    async def test_selector_resolves_via_frame_locator(self) -> None:
        """selector path: frame_locator(...).owner.element_handle().content_frame()."""
        page, frames = _make_page_with_frames()
        target_frame = frames[1]
        handle = SimpleNamespace(content_frame=AsyncMock(return_value=target_frame))
        # owner is NOT callable (SimpleNamespace) so the `callable(owner_attr)` branch is False.
        owner = SimpleNamespace(element_handle=AsyncMock(return_value=handle))
        page.frame_locator.return_value.owner = owner
        frame, info = await switch_frame_impl(page, selector="iframe.x", name=None, url_pattern=None)
        assert frame is target_frame
        assert info["index"] == 1
        assert info["url"] == target_frame.url
        assert info["name"] == target_frame.name

    @pytest.mark.anyio
    async def test_selector_callable_owner_invoked(self) -> None:
        """If owner is callable, it gets called once before .element_handle()."""
        page, frames = _make_page_with_frames()
        target_frame = frames[0]
        handle = SimpleNamespace(content_frame=AsyncMock(return_value=target_frame))
        owner = SimpleNamespace(element_handle=AsyncMock(return_value=handle))

        def _owner_call() -> Any:
            return owner

        page.frame_locator.return_value.owner = _owner_call
        frame, _info = await switch_frame_impl(page, selector="iframe", name=None, url_pattern=None)
        assert frame is target_frame

    @pytest.mark.anyio
    async def test_selector_no_element_handle_raises(self) -> None:
        """element_handle() returning None → 'no element matches iframe selector ...'."""
        page, _ = _make_page_with_frames()
        owner = SimpleNamespace(element_handle=AsyncMock(return_value=None))
        page.frame_locator.return_value.owner = owner
        with pytest.raises(RuntimeError, match=r"no element matches iframe selector"):
            await switch_frame_impl(page, selector="iframe.bad", name=None, url_pattern=None)

    @pytest.mark.anyio
    async def test_selector_no_content_frame_raises(self) -> None:
        """content_frame() returning None → 'no frame found for selector ...'."""
        page, _ = _make_page_with_frames()
        handle = SimpleNamespace(content_frame=AsyncMock(return_value=None))
        owner = SimpleNamespace(element_handle=AsyncMock(return_value=handle))
        page.frame_locator.return_value.owner = owner
        with pytest.raises(RuntimeError, match=r"no frame found for selector"):
            await switch_frame_impl(page, selector="iframe.x", name=None, url_pattern=None)


class TestSwitchFrameName:
    @pytest.mark.anyio
    async def test_name_path_resolves_via_page_frame(self) -> None:
        """name path: page.frame(name=...) returns the frame."""
        page, frames = _make_page_with_frames()
        target = frames[2]
        page.frame = MagicMock(return_value=target)
        frame, info = await switch_frame_impl(page, selector=None, name="frame-2", url_pattern=None)
        assert frame is target
        assert info["index"] == 2

    @pytest.mark.anyio
    async def test_name_not_found_raises(self) -> None:
        """page.frame(name=...) returning None → 'no frame with name=...'."""
        page, _ = _make_page_with_frames()
        page.frame = MagicMock(return_value=None)
        with pytest.raises(RuntimeError, match=r"no frame with name="):
            await switch_frame_impl(page, selector=None, name="missing", url_pattern=None)


class TestSwitchFrameUrlPattern:
    @pytest.mark.anyio
    async def test_url_pattern_compiles_to_regex(self) -> None:
        """url_pattern → page.frame(url=re.compile(pattern))."""
        page, frames = _make_page_with_frames()
        target = frames[0]
        page.frame = MagicMock(return_value=target)
        frame, _info = await switch_frame_impl(page, selector=None, name=None, url_pattern=r"frame-\d")
        assert frame is target
        # The kwarg passed to page.frame is a compiled regex.
        passed = page.frame.call_args.kwargs["url"]
        assert hasattr(passed, "pattern")
        assert passed.pattern == r"frame-\d"

    @pytest.mark.anyio
    async def test_url_pattern_no_match_raises(self) -> None:
        """page.frame(url=...) returning None → 'no frame matching url_pattern=...'."""
        page, _ = _make_page_with_frames()
        page.frame = MagicMock(return_value=None)
        with pytest.raises(RuntimeError, match=r"no frame matching url_pattern="):
            await switch_frame_impl(page, selector=None, name=None, url_pattern="x")


class TestSwitchFrameInfoIndex:
    @pytest.mark.anyio
    async def test_unfound_frame_index_minus_one(self) -> None:
        """Frame returned but not present in page.frames → index=-1."""
        page, _ = _make_page_with_frames()
        # Resolved frame is NOT in page.frames.
        rogue = MagicMock()
        rogue.url = "rogue"
        rogue.name = "rogue"
        page.frame = MagicMock(return_value=rogue)
        _frame, info = await switch_frame_impl(page, selector=None, name="x", url_pattern=None)
        assert info["index"] == -1


# ─── session/frames: list_frames_impl ──────────────────────────────────────


class TestListFrames:
    def test_returns_dict_per_frame(self) -> None:
        """One dict per frame, with index/name/url/is_active fields."""
        page, frames = _make_page_with_frames(num_frames=3)
        result = list_frames_impl(page, active_frame=frames[1])
        assert len(result) == 3
        for i, row in enumerate(result):
            assert row["index"] == i
            assert row["url"] == frames[i].url
            assert row["name"] == frames[i].name

    def test_active_frame_marked(self) -> None:
        """is_active=True only for the matching frame."""
        page, frames = _make_page_with_frames(num_frames=3)
        result = list_frames_impl(page, active_frame=frames[1])
        flags = [row["is_active"] for row in result]
        assert flags == [False, True, False]

    def test_no_active_frame_all_false(self) -> None:
        """active_frame=None → no rows have is_active=True."""
        page, _ = _make_page_with_frames(num_frames=2)
        result = list_frames_impl(page, active_frame=None)
        assert all(row["is_active"] is False for row in result)

    def test_empty_frames(self) -> None:
        """Page with no frames → empty list."""
        page = MagicMock()
        page.frames = []
        assert list_frames_impl(page, active_frame=None) == []


# ─── session/locators: build_locator ───────────────────────────────────────


class TestBuildLocatorValidation:
    def test_no_args_raises(self) -> None:
        """All None → ValueError listing the empty 'provided' list."""
        target = MagicMock()
        with pytest.raises(ValueError, match=r"exactly one of role/label/text/test_id must be set"):
            build_locator(target)

    def test_two_args_raises(self) -> None:
        """Multiple finder args → ValueError naming both."""
        target = MagicMock()
        with pytest.raises(ValueError, match=r"role"):
            build_locator(target, role="button", label="OK")


class TestBuildLocatorDispatch:
    def test_role_no_name_passes_no_kwargs(self) -> None:
        """role given, role_name=None → get_by_role with empty kwargs."""
        target = MagicMock()
        target.get_by_role.return_value = "result"
        result = build_locator(target, role="button")
        assert result == "result"
        target.get_by_role.assert_called_once_with("button")

    def test_role_with_name_passes_name_and_exact(self) -> None:
        """role + role_name → kwargs include name + exact (default False)."""
        target = MagicMock()
        build_locator(target, role="button", role_name="Save")
        target.get_by_role.assert_called_once_with("button", name="Save", exact=False)

    def test_role_exact_true_passthrough(self) -> None:
        """role_exact=True → kwargs has exact=True."""
        target = MagicMock()
        build_locator(target, role="button", role_name="Save", role_exact=True)
        target.get_by_role.assert_called_once_with("button", name="Save", exact=True)

    def test_label_dispatch(self) -> None:
        """label-only → get_by_label."""
        target = MagicMock()
        target.get_by_label.return_value = "lbl"
        assert build_locator(target, label="Email") == "lbl"
        target.get_by_label.assert_called_once_with("Email")

    def test_text_dispatch(self) -> None:
        """text-only → get_by_text."""
        target = MagicMock()
        target.get_by_text.return_value = "t"
        assert build_locator(target, text="Click me") == "t"
        target.get_by_text.assert_called_once_with("Click me")

    def test_test_id_dispatch(self) -> None:
        """test_id-only → get_by_test_id."""
        target = MagicMock()
        target.get_by_test_id.return_value = "tid"
        assert build_locator(target, test_id="submit-btn") == "tid"
        target.get_by_test_id.assert_called_once_with("submit-btn")


# ─── stabilize: render_stabilize_script ────────────────────────────────────


class TestStabilizeContent:
    def test_render_returns_constant_verbatim(self) -> None:
        """render_stabilize_script returns the STABILIZE_SCRIPT constant exactly."""
        assert render_stabilize_script() == STABILIZE_SCRIPT

    def test_render_is_pure_function(self) -> None:
        """Two calls return identical output (no parameterisation)."""
        assert render_stabilize_script() == render_stabilize_script()

    def test_script_freezes_date_now(self) -> None:
        """Script overrides Date.now."""
        assert "Date.now = frozenNow" in STABILIZE_SCRIPT

    def test_script_uses_frozen_epoch(self) -> None:
        """Frozen epoch is the documented 1700000000000 (2023-11-14)."""
        assert "1700000000000" in STABILIZE_SCRIPT

    def test_script_overrides_raf(self) -> None:
        """requestAnimationFrame is replaced with synchronous fire."""
        assert "window.requestAnimationFrame" in STABILIZE_SCRIPT
        assert "cancelAnimationFrame" in STABILIZE_SCRIPT

    def test_script_zeroes_animation_durations(self) -> None:
        """CSS rule zeros animation/transition durations."""
        assert "animation-duration: 0ms" in STABILIZE_SCRIPT
        assert "transition-duration: 0ms" in STABILIZE_SCRIPT
        assert "animation-delay: 0ms" in STABILIZE_SCRIPT
        assert "transition-delay: 0ms" in STABILIZE_SCRIPT

    def test_script_disables_smooth_scroll(self) -> None:
        """scroll-behavior: auto disables smooth scrolling."""
        assert "scroll-behavior: auto" in STABILIZE_SCRIPT

    def test_script_uses_important_to_override_user_styles(self) -> None:
        """!important is used so user styles can't beat us."""
        assert "!important" in STABILIZE_SCRIPT

    def test_script_handles_late_head_via_dom_content_loaded(self) -> None:
        """Falls back to DOMContentLoaded when document.head is missing."""
        assert "DOMContentLoaded" in STABILIZE_SCRIPT

    def test_script_swallows_exceptions_around_overrides(self) -> None:
        """Override blocks are wrapped in try/catch so absent globals don't break the page."""
        assert "try {" in STABILIZE_SCRIPT
        assert "catch (_)" in STABILIZE_SCRIPT
