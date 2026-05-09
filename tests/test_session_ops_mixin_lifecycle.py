# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.session.core_ops_mixin lifecycle.

Covers _drain_background_tasks (no-tasks fast path, completed-task drain,
pending-task cancellation, current-task exclusion, exception swallow) and
close (recorder finalize, video/trace/har/markdown/websocket fields,
trace-stop swallow + error-record, video-path resolution + swallow,
browser=None branch, finally always runs).
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


def _build(tmp_path: Path, *, page: Any = None, context: Any = None, **overrides: Any) -> SessionOpsMixin:
    inst = SessionOpsMixin.__new__(SessionOpsMixin)
    inst.page = page if page is not None else MagicMock()
    inst.context = context if context is not None else MagicMock()
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
    inst._target = lambda: inst.active_frame if inst.active_frame is not None else inst.page  # type: ignore[method-assign]
    for k, v in overrides.items():
        setattr(inst, k, v)
    return inst


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── _drain_background_tasks ───────────────────────────────────────────────


class TestDrainBackgroundTasks:
    @pytest.mark.anyio
    async def test_no_tasks_returns_quickly(self, tmp_path: Path) -> None:
        """Empty bg-task set → no-op return."""
        inst = _build(tmp_path)
        await inst._drain_background_tasks()

    @pytest.mark.anyio
    async def test_completed_task_drained_and_discarded(self, tmp_path: Path) -> None:
        """Already-done tasks are awaited and removed from _bg_tasks."""
        inst = _build(tmp_path)

        async def quick() -> int:
            return 7

        task = asyncio.create_task(quick())
        await asyncio.sleep(0)
        inst._bg_tasks.add(task)
        await inst._drain_background_tasks()
        assert task not in inst._bg_tasks

    @pytest.mark.anyio
    async def test_pending_task_cancelled_and_awaited(self, tmp_path: Path) -> None:
        """A long-running task hits the timeout, gets cancelled, then awaited."""
        inst = _build(tmp_path)
        inst._BG_TASK_DRAIN_TIMEOUT_SECONDS = 0.05

        async def long() -> None:
            await asyncio.sleep(5)

        task = asyncio.create_task(long())
        inst._bg_tasks.add(task)
        await inst._drain_background_tasks()
        assert task.cancelled() or task.done()

    @pytest.mark.anyio
    async def test_excludes_current_task_from_drain(self, tmp_path: Path) -> None:
        """The currently-running task is skipped — would deadlock otherwise."""
        inst = _build(tmp_path)

        async def caller() -> None:
            current = asyncio.current_task()
            assert current is not None
            inst._bg_tasks.add(current)
            await inst._drain_background_tasks()
            inst._bg_tasks.discard(current)

        await caller()

    @pytest.mark.anyio
    async def test_completed_task_exception_swallowed(self, tmp_path: Path) -> None:
        """A done task that raised must not propagate from drain."""
        inst = _build(tmp_path)

        async def boom() -> None:
            raise ValueError("nope")

        task = asyncio.create_task(boom())
        await asyncio.sleep(0)
        inst._bg_tasks.add(task)
        await inst._drain_background_tasks()


# ─── close ─────────────────────────────────────────────────────────────────


class TestClose:
    @pytest.mark.anyio
    async def test_close_records_and_finalizes_recorder(self, tmp_path: Path) -> None:
        """close() emits 'close' record + recorder.close()."""
        context = MagicMock()
        context.close = AsyncMock()
        inst = _build(tmp_path, context=context)
        await inst.close()
        action_names = [e[0] for e in inst.recorder.events]
        assert "close" in action_names
        assert inst.recorder.closed is True

    @pytest.mark.anyio
    async def test_close_event_fields_include_paths(self, tmp_path: Path) -> None:
        """video/trace/har/markdown/websocket paths appear in the close record."""
        context = MagicMock()
        context.close = AsyncMock()
        inst = _build(tmp_path, context=context)
        inst.video_path = tmp_path / "v.webm"
        inst.trace_path = tmp_path / "t.zip"
        inst.har_path = tmp_path / "h.har"
        inst.markdown_path = tmp_path / "m.md"
        inst.websocket_path = tmp_path / "w.jsonl"
        await inst.close()
        close_event = next(e for e in inst.recorder.events if e[0] == "close")
        kw = close_event[1]
        assert kw["video_path"].endswith("v.webm")
        assert kw["trace_path"].endswith("t.zip")
        assert kw["har_path"].endswith("h.har")
        assert kw["markdown_path"].endswith("m.md")
        assert kw["websocket_path"].endswith("w.jsonl")

    @pytest.mark.anyio
    async def test_close_paths_are_none_when_not_set(self, tmp_path: Path) -> None:
        """Each path field is None in the close event when its attr is None."""
        context = MagicMock()
        context.close = AsyncMock()
        inst = _build(tmp_path, context=context)
        await inst.close()
        kw = next(e for e in inst.recorder.events if e[0] == "close")[1]
        for key in ("video_path", "trace_path", "har_path", "markdown_path", "websocket_path"):
            assert kw[key] is None

    @pytest.mark.anyio
    async def test_trace_stop_called_when_trace_enabled(self, tmp_path: Path) -> None:
        """trace=True → context.tracing.stop(path=...)."""
        context = MagicMock()
        context.close = AsyncMock()
        context.tracing.stop = AsyncMock()
        inst = _build(tmp_path, context=context)
        inst.trace = True
        await inst.close()
        assert context.tracing.stop.await_count == 1
        assert inst.trace_path is not None
        assert str(inst.trace_path).endswith(".trace.zip")

    @pytest.mark.anyio
    async def test_trace_stop_failure_swallowed_and_recorded(self, tmp_path: Path) -> None:
        """tracing.stop() raising → trace_path reset to None + trace_stop_error event."""
        context = MagicMock()
        context.close = AsyncMock()
        context.tracing.stop = AsyncMock(side_effect=RuntimeError("trace dead"))
        inst = _build(tmp_path, context=context)
        inst.trace = True
        await inst.close()
        action_names = [e[0] for e in inst.recorder.events]
        assert "trace_stop_error" in action_names
        assert inst.trace_path is None

    @pytest.mark.anyio
    async def test_video_path_resolved_from_video_object(self, tmp_path: Path) -> None:
        """When _video is set, .path() resolves and stamps self.video_path."""
        context = MagicMock()
        context.close = AsyncMock()
        video = MagicMock()
        video.path = AsyncMock(return_value=str(tmp_path / "vid.webm"))
        inst = _build(tmp_path, context=context)
        inst._video = video
        await inst.close()
        assert inst.video_path == tmp_path / "vid.webm"

    @pytest.mark.anyio
    async def test_video_path_resolution_failure_swallowed(self, tmp_path: Path) -> None:
        """video.path() raising → video_path stays None, no exception."""
        context = MagicMock()
        context.close = AsyncMock()
        video = MagicMock()
        video.path = AsyncMock(side_effect=RuntimeError("not finalised"))
        inst = _build(tmp_path, context=context)
        inst._video = video
        await inst.close()
        assert inst.video_path is None

    @pytest.mark.anyio
    async def test_close_browser_when_set(self, tmp_path: Path) -> None:
        """browser is not None → browser.close() awaited."""
        context = MagicMock()
        context.close = AsyncMock()
        browser = MagicMock()
        browser.close = AsyncMock()
        inst = _build(tmp_path, context=context)
        inst.browser = browser
        await inst.close()
        assert browser.close.await_count == 1

    @pytest.mark.anyio
    async def test_close_skips_browser_close_when_none(self, tmp_path: Path) -> None:
        """browser is None (e.g. context-only persistent profile) → no AttributeError."""
        context = MagicMock()
        context.close = AsyncMock()
        inst = _build(tmp_path, context=context)
        inst.browser = None
        await inst.close()

    @pytest.mark.anyio
    async def test_close_finally_runs_even_when_context_close_fails(self, tmp_path: Path) -> None:
        """context.close() failure must not skip the finally block (recorder finalize)."""
        context = MagicMock()
        context.close = AsyncMock(side_effect=RuntimeError("ctx-close"))
        inst = _build(tmp_path, context=context)
        with pytest.raises(RuntimeError, match=r"ctx-close"):
            await inst.close()
        assert inst.recorder.closed is True
