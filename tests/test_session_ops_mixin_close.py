# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for SessionOpsMixin.close + _drain_background_tasks.

The other surfaces of core_ops_mixin (switch_frame / hover / select_option /
drag / navigate_back / resize / open_url / diagnostic_bundle) are covered in
test_session_ops_mixin_actions.py + _diagnostic.py. Pins:
- close() trace branch (success + error paths)
- close() video-path resolution (success + failure)
- close() browser=None vs browser=present
- close() recorder.record fields shape
- close() recorder.close() always called via finally
- _drain_background_tasks: empty set short-circuit; current-task exclusion;
  done-task result swallow; pending-task cancel-after-timeout
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session.core_ops_mixin import SessionOpsMixin


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def record(self, action: str, **kwargs: Any) -> None:
        self.events.append((action, kwargs))

    def close(self) -> None:
        self.closed = True


def _build(tmp_path: Path, **overrides: Any) -> SessionOpsMixin:
    inst = SessionOpsMixin.__new__(SessionOpsMixin)
    inst.page = MagicMock()
    inst.context = MagicMock()
    inst.context.close = AsyncMock()
    inst.context.tracing = MagicMock()
    inst.context.tracing.stop = AsyncMock()
    inst.browser = None
    inst.recorder = _Recorder()
    inst.console = []
    inst.pages = [inst.page]
    inst.active_frame = None
    inst.video_path = None
    inst.trace_path = None
    inst.har_path = None
    inst.markdown_path = None
    inst.websocket_path = None
    inst.trace = False
    inst._video = None
    inst._bg_tasks = set()
    inst.instance_id = "abc123"
    inst.log_path = tmp_path / "session.jsonl"
    inst.log_path.write_text("", encoding="utf-8")
    inst._target = lambda: inst.page  # type: ignore[method-assign]
    for k, v in overrides.items():
        setattr(inst, k, v)
    return inst


def _close_event(rec: _Recorder) -> dict[str, Any]:
    """Return the kwargs of the 'close' event, asserting it was emitted exactly once."""
    matches = [kwargs for (name, kwargs) in rec.events if name == "close"]
    assert len(matches) == 1, f"expected exactly one 'close' event, got {len(matches)}"
    return matches[0]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── close: happy paths ──────────────────────────────────────────────────────


class TestCloseHappy:
    @pytest.mark.anyio
    async def test_records_close_event_with_all_path_fields(self, tmp_path: Path) -> None:
        """close emits a 'close' event with every path slot (Nones serialise as None)."""
        inst = _build(tmp_path)
        await inst.close()
        event = _close_event(inst.recorder)
        # All path fields present, all None when not set.
        assert set(event.keys()) == {"video_path", "trace_path", "har_path", "markdown_path", "websocket_path"}
        assert all(v is None for v in event.values())

    @pytest.mark.anyio
    async def test_close_event_serialises_paths_to_str(self, tmp_path: Path) -> None:
        """Non-None paths are stringified — never serialised as Path objects."""
        video = tmp_path / "v.webm"
        trace = tmp_path / "t.zip"
        har = tmp_path / "h.har"
        markdown = tmp_path / "m.md"
        websocket = tmp_path / "w.json"
        inst = _build(
            tmp_path,
            video_path=video,
            trace_path=trace,
            har_path=har,
            markdown_path=markdown,
            websocket_path=websocket,
        )
        await inst.close()
        event = _close_event(inst.recorder)
        assert event["video_path"] == str(video)
        assert event["trace_path"] == str(trace)
        assert event["har_path"] == str(har)
        assert event["markdown_path"] == str(markdown)
        assert event["websocket_path"] == str(websocket)

    @pytest.mark.anyio
    async def test_close_calls_context_close(self, tmp_path: Path) -> None:
        """The browser context is closed."""
        inst = _build(tmp_path)
        await inst.close()
        inst.context.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_recorder_close_called_in_finally(self, tmp_path: Path) -> None:
        """recorder.close() is called as the last step (finally block)."""
        inst = _build(tmp_path)
        await inst.close()
        assert inst.recorder.closed is True

    @pytest.mark.anyio
    async def test_browser_none_skips_browser_close(self, tmp_path: Path) -> None:
        """When browser is None, no browser-close call is made (no AttributeError)."""
        inst = _build(tmp_path, browser=None)
        await inst.close()  # must not raise
        assert inst.recorder.closed is True

    @pytest.mark.anyio
    async def test_browser_present_is_closed(self, tmp_path: Path) -> None:
        """When browser is set, browser.close() is awaited."""
        browser = MagicMock()
        browser.close = AsyncMock()
        inst = _build(tmp_path, browser=browser)
        await inst.close()
        browser.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_browser_for_close_handle_is_used_when_browser_none(self, tmp_path: Path) -> None:
        """Persistent contexts may expose a browser handle separately; close() uses it."""
        close_handle = MagicMock()
        close_handle.close = AsyncMock()
        inst = _build(tmp_path, browser=None, _browser_for_close=close_handle)
        await inst.close()
        close_handle.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_close_handle_failure_does_not_skip_terminal_recorder_events(self, tmp_path: Path) -> None:
        """If context.close() already terminated the browser, the captured
        close_handle.close() call raises. The finally block must keep going so
        the JSONL recording still gets its terminal 'close' event and the
        recorder file handle is closed. Without the try/except inside the
        finally, the JSONL would be left mid-stream and parsers downstream
        (tail, replay, golden compare) would see a truncated session."""
        close_handle = MagicMock()
        close_handle.close = AsyncMock(side_effect=RuntimeError("target closed"))
        inst = _build(tmp_path, browser=None, _browser_for_close=close_handle)
        await inst.close()  # must NOT raise
        kinds = [name for (name, _) in inst.recorder.events]
        assert "close" in kinds, "terminal close event must still be recorded"
        assert inst.recorder.closed is True, "recorder.close() must still run"


# ─── close: trace branch ─────────────────────────────────────────────────────


class TestCloseTrace:
    @pytest.mark.anyio
    async def test_trace_true_writes_trace_path_and_stops(self, tmp_path: Path) -> None:
        """trace=True → trace_path = log_path.with_suffix('.trace.zip'); tracing.stop awaited."""
        inst = _build(tmp_path, trace=True)
        await inst.close()
        expected = inst.log_path.with_suffix(".trace.zip")
        assert inst.trace_path == expected
        inst.context.tracing.stop.assert_awaited_once_with(path=str(expected))
        # Recorded event reflects the trace path.
        event = _close_event(inst.recorder)
        assert event["trace_path"] == str(expected)

    @pytest.mark.anyio
    async def test_trace_stop_error_records_event_and_resets_path(self, tmp_path: Path) -> None:
        """tracing.stop exception → records trace_stop_error AND resets trace_path to None."""
        inst = _build(tmp_path, trace=True)
        inst.context.tracing.stop = AsyncMock(side_effect=RuntimeError("trace boom"))
        await inst.close()
        # trace_stop_error event present.
        kinds = [name for (name, _) in inst.recorder.events]
        assert "trace_stop_error" in kinds
        # trace_path reset to None.
        assert inst.trace_path is None
        # The close event reflects the reset (trace_path=None).
        event = _close_event(inst.recorder)
        assert event["trace_path"] is None

    @pytest.mark.anyio
    async def test_trace_stop_error_carries_repr(self, tmp_path: Path) -> None:
        """The recorded trace_stop_error carries repr(exception)."""
        inst = _build(tmp_path, trace=True)
        exc = RuntimeError("trace boom")
        inst.context.tracing.stop = AsyncMock(side_effect=exc)
        await inst.close()
        for name, kwargs in inst.recorder.events:
            if name == "trace_stop_error":
                assert kwargs["error"] == repr(exc)
                return
        pytest.fail("trace_stop_error event not recorded")

    @pytest.mark.anyio
    async def test_trace_false_skips_tracing_stop(self, tmp_path: Path) -> None:
        """trace=False → tracing.stop is never awaited, trace_path stays None."""
        inst = _build(tmp_path, trace=False)
        await inst.close()
        inst.context.tracing.stop.assert_not_awaited()
        assert inst.trace_path is None


# ─── close: video resolution ────────────────────────────────────────────────


class TestCloseVideo:
    @pytest.mark.anyio
    async def test_video_path_resolved_after_context_close(self, tmp_path: Path) -> None:
        """When _video is set, .path() is awaited and stored as video_path Path."""
        video_obj = MagicMock()
        resolved_str = str(tmp_path / "video.webm")
        video_obj.path = AsyncMock(return_value=resolved_str)
        inst = _build(tmp_path, _video=video_obj)
        await inst.close()
        video_obj.path.assert_awaited_once()
        assert inst.video_path == Path(resolved_str)

    @pytest.mark.anyio
    async def test_video_path_failure_swallowed(self, tmp_path: Path) -> None:
        """If video.path() raises, video_path stays None and close still completes."""
        video_obj = MagicMock()
        video_obj.path = AsyncMock(side_effect=RuntimeError("video boom"))
        inst = _build(tmp_path, _video=video_obj)
        await inst.close()
        assert inst.video_path is None
        # Recorder still closed (finally ran).
        assert inst.recorder.closed is True

    @pytest.mark.anyio
    async def test_video_none_skips_resolution(self, tmp_path: Path) -> None:
        """_video=None → no path() call, video_path stays None."""
        inst = _build(tmp_path, _video=None)
        await inst.close()
        assert inst.video_path is None


# ─── close: failure of context.close still runs finally ─────────────────────


class TestCloseFinallyAlwaysRuns:
    @pytest.mark.anyio
    async def test_context_close_failure_propagates_but_finally_still_runs(self, tmp_path: Path) -> None:
        """If context.close() raises, the close event + recorder.close() still happen."""
        inst = _build(tmp_path)
        inst.context.close = AsyncMock(side_effect=RuntimeError("ctx boom"))
        with pytest.raises(RuntimeError, match=r"ctx boom"):
            await inst.close()
        # Finally block ran: recorder.close + close event both observed.
        assert inst.recorder.closed is True
        kinds = [name for (name, _) in inst.recorder.events]
        assert "close" in kinds


# ─── _drain_background_tasks ────────────────────────────────────────────────


class TestDrainBackgroundTasks:
    @pytest.mark.anyio
    async def test_empty_set_returns_immediately(self, tmp_path: Path) -> None:
        """No bg tasks → no asyncio.wait, no exceptions."""
        inst = _build(tmp_path)
        await inst._drain_background_tasks()  # must not raise

    @pytest.mark.anyio
    async def test_completed_task_result_consumed(self, tmp_path: Path) -> None:
        """A finished bg task's result is consumed (no 'result never awaited' warning)."""
        inst = _build(tmp_path)

        async def _quick() -> str:
            return "done"

        task = asyncio.create_task(_quick())
        await asyncio.sleep(0)  # let it complete
        inst._bg_tasks.add(task)

        await inst._drain_background_tasks()
        # Task is removed from _bg_tasks.
        assert task not in inst._bg_tasks

    @pytest.mark.anyio
    async def test_completed_task_exception_swallowed(self, tmp_path: Path) -> None:
        """A finished bg task that raised — exception is swallowed, drain returns normally."""
        inst = _build(tmp_path)

        async def _fail() -> None:
            raise RuntimeError("bg boom")

        task = asyncio.create_task(_fail())
        await asyncio.sleep(0)
        inst._bg_tasks.add(task)

        await inst._drain_background_tasks()  # must not raise
        assert task not in inst._bg_tasks

    @pytest.mark.anyio
    async def test_pending_task_cancelled_after_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A long-running bg task is cancelled when drain timeout expires."""
        # Shorten the drain timeout so the test isn't slow.
        monkeypatch.setattr(SessionOpsMixin, "_BG_TASK_DRAIN_TIMEOUT_SECONDS", 0.05)
        inst = _build(tmp_path)

        async def _hang() -> None:
            await asyncio.sleep(10)

        task = asyncio.create_task(_hang())
        inst._bg_tasks.add(task)

        await inst._drain_background_tasks()
        # Drain returned, task was cancelled, and removed from set.
        assert task.cancelled() or task.done()
        assert task not in inst._bg_tasks

    @pytest.mark.anyio
    async def test_current_task_excluded_drains_others(self, tmp_path: Path) -> None:
        """Current task is filtered out of the drain set; sibling tasks still drain.

        If exclusion broke, asyncio.wait would hang on the current task — the
        test would block until pytest's session-level timeout, not the
        per-call timeout (we can't safely wait_for around drain itself, as
        wait_for wraps in a *new* task, sidestepping the exclusion).
        """
        inst = _build(tmp_path)
        running = asyncio.current_task()
        assert running is not None

        async def _quick() -> None:
            return None

        other = asyncio.create_task(_quick())
        await asyncio.sleep(0)  # let other complete
        inst._bg_tasks.add(running)
        inst._bg_tasks.add(other)

        await inst._drain_background_tasks()

        # Current task excluded → never discarded. Other task drained → discarded.
        assert running in inst._bg_tasks
        assert other not in inst._bg_tasks

    @pytest.mark.anyio
    async def test_completed_cancelled_task_skipped_in_result_loop(self, tmp_path: Path) -> None:
        """A done-but-cancelled task takes the 'continue' branch instead of .result()."""
        inst = _build(tmp_path)

        async def _hang() -> None:
            await asyncio.sleep(10)

        task = asyncio.create_task(_hang())
        task.cancel()
        await asyncio.sleep(0)  # allow cancellation to propagate
        inst._bg_tasks.add(task)

        await inst._drain_background_tasks()
        assert task not in inst._bg_tasks

    @pytest.mark.anyio
    async def test_drain_called_by_close(self, tmp_path: Path) -> None:
        """close() invokes _drain_background_tasks (dropping any pending bg work)."""
        inst = _build(tmp_path)

        drained = False

        async def _quick() -> None:
            nonlocal drained
            drained = True

        task = asyncio.create_task(_quick())
        await asyncio.sleep(0)  # let it complete
        inst._bg_tasks.add(task)

        await inst.close()
        assert drained is True
        # _bg_tasks emptied.
        assert task not in inst._bg_tasks

    @pytest.mark.anyio
    async def test_tasks_added_during_drain_are_also_drained(self, tmp_path: Path) -> None:
        """A bg task whose done-callback schedules another bg task must be drained too.

        Real-world example: ``capture_markdown`` is scheduled as a bg task by
        ``_schedule_markdown_capture``. A ``framenavigated`` callback firing
        during ``asyncio.wait`` adds a fresh markdown-capture task to
        ``_bg_tasks`` AFTER we snapshotted the set — without the iterative
        drain that second task leaks a reference to the closed session's
        recorder and is never cancelled.
        """
        inst = _build(tmp_path)
        second_cancelled = False

        async def _second_hang() -> None:
            nonlocal second_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                second_cancelled = True
                raise

        async def _first_quick() -> None:
            return None

        first = asyncio.create_task(_first_quick())
        # When `first` finishes, schedule a NEW bg task and register it in
        # _bg_tasks — mirroring how _schedule_markdown_capture wires its own
        # cleanup callback. The drain must catch this task even though it
        # didn't exist in the initial snapshot.
        second_box: dict[str, Any] = {}

        def _spawn_second(_done: Any) -> None:
            second = asyncio.create_task(_second_hang())
            inst._bg_tasks.add(second)
            second_box["task"] = second

        first.add_done_callback(_spawn_second)
        await asyncio.sleep(0)  # let `first` complete and the callback run
        inst._bg_tasks.add(first)

        # Tight timeout so the test isn't slow if the iterative drain regresses.
        SessionOpsMixin._BG_TASK_DRAIN_TIMEOUT_SECONDS = 0.05  # type: ignore[misc]
        try:
            await inst._drain_background_tasks()
        finally:
            SessionOpsMixin._BG_TASK_DRAIN_TIMEOUT_SECONDS = 1.0  # type: ignore[misc]

        # Both tasks should be drained: the original short one AND the one
        # spawned mid-drain by its done-callback.
        assert first not in inst._bg_tasks
        second = second_box["task"]
        assert second not in inst._bg_tasks
        assert second.done()
        assert second_cancelled, "the second (mid-drain) task was never cancelled"
