# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.session.core_ops_mixin action ops.

Covers: switch_frame / reset_frame / list_frames; hover; select_option
kwarg construction (value/label/index — including 0); drag; navigate_back's
ok-flag from response truthiness; resize viewport-dict shape; open_url
tab/window branches incl. invalid-target guard, race-protection against
double-append, navigation-failure swallow.
"""

from __future__ import annotations

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


# ─── switch_frame / reset_frame / list_frames ──────────────────────────────


class TestFrameOps:
    @pytest.mark.anyio
    async def test_switch_frame_records_and_sets_active(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """switch_frame stores the resolved frame on self.active_frame and records."""
        from octowright.session import frames as _frames

        info = {"index": 2, "url": "https://x", "name": "iframe-1"}
        fake_frame = MagicMock()

        async def fake_switch(page: Any, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
            return fake_frame, info

        monkeypatch.setattr(_frames, "switch_frame_impl", fake_switch)
        inst = _build(tmp_path)
        out = await inst.switch_frame(selector="iframe", name=None, url_pattern=None)
        assert inst.active_frame is fake_frame
        assert out == info
        events = inst.recorder.events
        assert len(events) == 1
        assert events[0][0] == "switch_frame"
        assert events[0][1]["index"] == 2
        assert events[0][1]["frame_url"] == "https://x"
        assert events[0][1]["frame_name"] == "iframe-1"

    @pytest.mark.anyio
    async def test_reset_frame_clears_active_and_records(self, tmp_path: Path) -> None:
        """reset_frame sets active_frame to None and records a reset event."""
        inst = _build(tmp_path)
        inst.active_frame = MagicMock()
        out = await inst.reset_frame()
        assert inst.active_frame is None
        assert out == {"ok": True, "active_frame": None}
        assert inst.recorder.events[0][0] == "reset_frame"

    def test_list_frames_delegates_to_impl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_frames forwards page + active_frame to frames.list_frames_impl."""
        from octowright.session import frames as _frames

        captured: dict[str, Any] = {}

        def fake_list(page: Any, active: Any) -> list[dict[str, Any]]:
            captured["page"] = page
            captured["active"] = active
            return [{"index": 0, "name": "main", "url": "u", "is_active": True}]

        monkeypatch.setattr(_frames, "list_frames_impl", fake_list)
        inst = _build(tmp_path)
        active = MagicMock()
        inst.active_frame = active
        out = inst.list_frames()
        assert out == [{"index": 0, "name": "main", "url": "u", "is_active": True}]
        assert captured["page"] is inst.page
        assert captured["active"] is active


# ─── hover / select_option / drag ──────────────────────────────────────────


class TestActionOps:
    @pytest.mark.anyio
    async def test_hover_records_selector(self, tmp_path: Path) -> None:
        """hover dispatches with timeout + recorder emit."""
        page = MagicMock()
        page.hover = AsyncMock()
        inst = _build(tmp_path, page=page)
        await inst.hover("#x")
        assert page.hover.await_count == 1
        call = page.hover.await_args
        assert call.args == ("#x",)
        assert "timeout" in call.kwargs
        assert inst.recorder.events == [("hover", {"selector": "#x"})]

    @pytest.mark.anyio
    async def test_select_option_value_only_kwarg(self, tmp_path: Path) -> None:
        """value passed without label/index → only `value` lands in select_option kwargs."""
        page = MagicMock()
        page.select_option = AsyncMock(return_value=["v"])
        inst = _build(tmp_path, page=page)
        out = await inst.select_option("#sel", value="v")
        kw = page.select_option.await_args.kwargs
        assert "value" in kw
        assert "label" not in kw
        assert "index" not in kw
        assert out == {"ok": True, "selected": ["v"]}

    @pytest.mark.anyio
    async def test_select_option_label_only_kwarg(self, tmp_path: Path) -> None:
        """label-only → only label kwarg."""
        page = MagicMock()
        page.select_option = AsyncMock(return_value=["L"])
        inst = _build(tmp_path, page=page)
        await inst.select_option("#sel", label="L")
        kw = page.select_option.await_args.kwargs
        assert kw.get("label") == "L"
        assert "value" not in kw
        assert "index" not in kw

    @pytest.mark.anyio
    async def test_select_option_index_only_kwarg(self, tmp_path: Path) -> None:
        """index-only → only index kwarg (zero is valid, not falsy-skipped)."""
        page = MagicMock()
        page.select_option = AsyncMock(return_value=["x"])
        inst = _build(tmp_path, page=page)
        await inst.select_option("#sel", index=0)
        kw = page.select_option.await_args.kwargs
        assert kw.get("index") == 0
        assert "value" not in kw
        assert "label" not in kw

    @pytest.mark.anyio
    async def test_select_option_record_carries_all_three_args(self, tmp_path: Path) -> None:
        """recorder records value/label/index even when None — for replay symmetry."""
        page = MagicMock()
        page.select_option = AsyncMock(return_value=["v"])
        inst = _build(tmp_path, page=page)
        await inst.select_option("#sel", value="v")
        ev = inst.recorder.events[0]
        assert ev[0] == "select_option"
        assert ev[1] == {"selector": "#sel", "value": "v", "label": None, "index": None}

    @pytest.mark.anyio
    async def test_drag_records_source_and_target(self, tmp_path: Path) -> None:
        """drag fires drag_and_drop and records source/target."""
        page = MagicMock()
        page.drag_and_drop = AsyncMock()
        inst = _build(tmp_path, page=page)
        await inst.drag("#a", "#b")
        assert page.drag_and_drop.await_args.args == ("#a", "#b")
        assert inst.recorder.events == [("drag", {"source": "#a", "target": "#b"})]


# ─── navigate_back / resize ────────────────────────────────────────────────


class TestNavigateBackResize:
    @pytest.mark.anyio
    async def test_navigate_back_ok_when_response_present(self, tmp_path: Path) -> None:
        """ok=True comes from `response is not None`."""
        page = MagicMock()
        page.go_back = AsyncMock(return_value=MagicMock())
        page.title = AsyncMock(return_value="prev")
        page.url = "https://prev"
        inst = _build(tmp_path, page=page)
        out = await inst.navigate_back()
        assert out == {"ok": True, "url": "https://prev", "title": "prev"}
        assert inst.recorder.events == [("navigate_back", {"url": "https://prev"})]

    @pytest.mark.anyio
    async def test_navigate_back_ok_false_when_response_none(self, tmp_path: Path) -> None:
        """No history → go_back returns None → ok=False."""
        page = MagicMock()
        page.go_back = AsyncMock(return_value=None)
        page.title = AsyncMock(return_value="curr")
        page.url = "https://curr"
        inst = _build(tmp_path, page=page)
        out = await inst.navigate_back()
        assert out["ok"] is False

    @pytest.mark.anyio
    async def test_resize_passes_dict_and_records(self, tmp_path: Path) -> None:
        """Viewport resize uses the dict shape Playwright expects."""
        page = MagicMock()
        page.set_viewport_size = AsyncMock()
        inst = _build(tmp_path, page=page)
        out = await inst.resize(800, 600)
        assert page.set_viewport_size.await_args.args == ({"width": 800, "height": 600},)
        assert out == {"ok": True, "width": 800, "height": 600}
        assert inst.recorder.events[0] == ("resize", {"width": 800, "height": 600})


# ─── open_url ──────────────────────────────────────────────────────────────


class _PopupCtx:
    """Async-context stand-in for page.expect_popup()."""

    def __init__(self, page: Any) -> None:
        self._page = page

    async def __aenter__(self) -> _PopupCtx:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    @property
    def value(self) -> Any:
        async def _val() -> Any:
            return self._page

        return _val()


class TestOpenUrl:
    @pytest.mark.anyio
    async def test_invalid_target_raises(self, tmp_path: Path) -> None:
        """target ∈ {tab, window} guard."""
        inst = _build(tmp_path)
        with pytest.raises(ValueError, match=r"target must be 'tab' or 'window', got"):
            await inst.open_url("https://x", target="popup")

    @pytest.mark.anyio
    async def test_tab_target_creates_new_page_in_context(self, tmp_path: Path) -> None:
        """target='tab' → context.new_page + goto. Page appended to self.pages."""
        new_page = MagicMock()
        new_page.url = "https://x/landed"
        new_page.goto = AsyncMock()
        context = MagicMock()
        context.new_page = AsyncMock(return_value=new_page)
        inst = _build(tmp_path, context=context)
        out = await inst.open_url("https://x")
        assert new_page in inst.pages
        assert out["ok"] is True
        assert out["target"] == "tab"
        assert out["url"] == "https://x/landed"
        assert out["page_index"] == inst.pages.index(new_page)

    @pytest.mark.anyio
    async def test_tab_goto_failure_swallowed(self, tmp_path: Path) -> None:
        """Navigation timeout/error doesn't fail open_url — page still tracked."""
        new_page = MagicMock()
        new_page.url = "about:blank"
        new_page.goto = AsyncMock(side_effect=RuntimeError("nav timeout"))
        context = MagicMock()
        context.new_page = AsyncMock(return_value=new_page)
        inst = _build(tmp_path, context=context)
        out = await inst.open_url("https://x")
        assert out["ok"] is True
        assert new_page in inst.pages

    @pytest.mark.anyio
    async def test_window_target_uses_popup(self, tmp_path: Path) -> None:
        """target='window' goes through page.expect_popup + window.open evaluate."""
        new_page = MagicMock()
        new_page.url = "https://popup"
        new_page.wait_for_load_state = AsyncMock()
        page = MagicMock()
        page.expect_popup = MagicMock(return_value=_PopupCtx(new_page))
        page.evaluate = AsyncMock()
        inst = _build(tmp_path, page=page)
        out = await inst.open_url("https://popup", target="window", width=800, height=600)
        assert out["target"] == "window"
        assert new_page in inst.pages
        eval_args = page.evaluate.await_args.args
        assert eval_args[1] == {"u": "https://popup", "w": 800, "h": 600}

    @pytest.mark.anyio
    async def test_window_load_state_failure_swallowed(self, tmp_path: Path) -> None:
        """wait_for_load_state raising → still returns ok with the new page."""
        new_page = MagicMock()
        new_page.url = "https://popup"
        new_page.wait_for_load_state = AsyncMock(side_effect=RuntimeError("load timeout"))
        page = MagicMock()
        page.expect_popup = MagicMock(return_value=_PopupCtx(new_page))
        page.evaluate = AsyncMock()
        inst = _build(tmp_path, page=page)
        out = await inst.open_url("https://popup", target="window")
        assert out["ok"] is True

    @pytest.mark.anyio
    async def test_open_url_skips_pages_append_when_already_present(self, tmp_path: Path) -> None:
        """Race-protection: if pool listener already appended new_page, don't double-add."""
        new_page = MagicMock()
        new_page.url = "https://x"
        new_page.goto = AsyncMock()
        context = MagicMock()
        context.new_page = AsyncMock(return_value=new_page)
        inst = _build(tmp_path, context=context)
        inst.pages.append(new_page)  # simulate listener got there first
        before_len = len(inst.pages)
        await inst.open_url("https://x")
        assert len(inst.pages) == before_len
