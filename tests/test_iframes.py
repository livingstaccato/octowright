from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octowright.recorder import Recorder
from octowright.session import BrowserSession


# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------


class FakeFrame:
    def __init__(self, name: str = "", url: str = "about:blank") -> None:
        self.name = name
        self.url = url
        self.click = AsyncMock()
        self.type = AsyncMock()
        self.fill = AsyncMock()
        self.evaluate = AsyncMock(return_value="frame-result")
        self.wait_for_selector = AsyncMock()
        self.wait_for_function = AsyncMock()


class FakePage:
    def __init__(self) -> None:
        self.url = "https://example.com"
        self.click = AsyncMock()
        self.type = AsyncMock()
        self.fill = AsyncMock()
        self.evaluate = AsyncMock(return_value="page-result")
        self.wait_for_selector = AsyncMock()
        self.wait_for_function = AsyncMock()
        self.wait_for_load_state = AsyncMock()
        self.keyboard = MagicMock()
        self.keyboard.press = AsyncMock()
        self.frames: list[Any] = []
        self._on_handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self._on_handlers[event] = handler

    def frame(self, *, name: str | None = None, url: Any = None) -> FakeFrame | None:
        for f in self.frames:
            if name is not None and f.name == name:
                return f
            if url is not None:
                # url may be a compiled regex
                import re
                pattern = url if isinstance(url, re.Pattern) else re.compile(url)
                if pattern.search(f.url):
                    return f
        return None

    def frame_locator(self, selector: str) -> Any:  # noqa: ARG002
        locator = MagicMock()
        handle = AsyncMock()
        frame = self.frames[0] if self.frames else None
        handle.content_frame = AsyncMock(return_value=frame)
        locator.owner.return_value.element_handle = AsyncMock(return_value=handle)
        return locator


def _make_session(tmp_path: Path) -> BrowserSession:
    log_path = tmp_path / "test.jsonl"
    recorder = Recorder(log_path)
    page = FakePage()
    return BrowserSession(
        instance_id="iframe-test",
        kind="chromium",
        label=None,
        url="https://example.com",
        browser=None,  # type: ignore[arg-type]
        context=MagicMock(),
        page=page,  # type: ignore[arg-type]
        recorder=recorder,
        log_path=log_path,
    )


# ---------------------------------------------------------------------------
# _target routing
# ---------------------------------------------------------------------------


def test_target_returns_page_when_no_frame(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    assert s._target() is s.page


def test_target_returns_active_frame_when_set(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    fake_frame = FakeFrame(name="inner")
    s.active_frame = fake_frame
    assert s._target() is fake_frame


# ---------------------------------------------------------------------------
# click / fill / evaluate / wait_for route through _target
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_click_uses_page_when_no_frame(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    await s.click("#btn")
    s.page.click.assert_called_once_with("#btn", timeout=s.page.click.call_args[1]["timeout"])  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_click_uses_frame_when_active(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    frame = FakeFrame(name="inner")
    s.active_frame = frame
    await s.click("#btn")
    frame.click.assert_called_once()
    s.page.click.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_fill_uses_frame_when_active(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    frame = FakeFrame(name="inner")
    s.active_frame = frame
    await s.fill("#input", "hello")
    frame.fill.assert_called_once()
    s.page.fill.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_evaluate_uses_frame_when_active(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    frame = FakeFrame(name="inner")
    s.active_frame = frame
    result = await s.evaluate("1+1")
    frame.evaluate.assert_called_once_with("1+1")
    s.page.evaluate.assert_not_called()  # type: ignore[attr-defined]
    assert result == "frame-result"


@pytest.mark.anyio
async def test_wait_for_selector_uses_frame_when_active(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    frame = FakeFrame(name="inner")
    s.active_frame = frame
    await s.wait_for("#el", None, None)
    frame.wait_for_selector.assert_called_once()
    s.page.wait_for_selector.assert_not_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# reset_frame
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reset_frame_clears_active_frame(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    s.active_frame = FakeFrame(name="inner")
    result = await s.reset_frame()
    assert s.active_frame is None
    assert result["active_frame"] is None


# ---------------------------------------------------------------------------
# list_frames
# ---------------------------------------------------------------------------


def test_list_frames_shape(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    main = FakeFrame(name="", url="https://example.com")
    child = FakeFrame(name="inner", url="https://widget.example.com")
    s.page.frames = [main, child]  # type: ignore[attr-defined]
    frames = s.list_frames()
    assert len(frames) == 2
    assert frames[0] == {"index": 0, "name": "", "url": "https://example.com", "is_active": False}
    assert frames[1] == {"index": 1, "name": "inner", "url": "https://widget.example.com", "is_active": False}


def test_list_frames_marks_active(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    main = FakeFrame(name="", url="https://example.com")
    child = FakeFrame(name="inner", url="https://widget.example.com")
    s.page.frames = [main, child]  # type: ignore[attr-defined]
    s.active_frame = child
    frames = s.list_frames()
    assert frames[1]["is_active"] is True
    assert frames[0]["is_active"] is False


# ---------------------------------------------------------------------------
# switch_frame by name
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_switch_frame_by_name_sets_active(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    inner = FakeFrame(name="inner", url="https://widget.example.com")
    s.page.frames = [FakeFrame(name="", url="https://example.com"), inner]  # type: ignore[attr-defined]
    result = await s.switch_frame(name="inner")
    assert s.active_frame is inner
    assert result["name"] == "inner"
    assert result["index"] == 1


@pytest.mark.anyio
async def test_switch_frame_by_name_raises_if_not_found(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    s.page.frames = []  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="no frame with name"):
        await s.switch_frame(name="missing")


# ---------------------------------------------------------------------------
# switch_frame by url_pattern
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_switch_frame_by_url_pattern(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    child = FakeFrame(name="stripe", url="https://js.stripe.com/v3/")
    s.page.frames = [FakeFrame(name="", url="https://example.com"), child]  # type: ignore[attr-defined]
    result = await s.switch_frame(url_pattern=r"stripe\.com")
    assert s.active_frame is child
    assert "stripe" in result["url"]


# ---------------------------------------------------------------------------
# switch_frame by selector
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_switch_frame_by_selector(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    child = FakeFrame(name="inner", url="https://widget.example.com")
    s.page.frames = [FakeFrame(name="", url="https://example.com"), child]  # type: ignore[attr-defined]
    # FakePage.frame_locator returns the first frame (index 0) in frames list,
    # but we specifically need it to return child. Patch it.
    s.page.frames.insert(0, child)  # type: ignore[attr-defined]
    # Rebuild so the locator's content_frame returns child
    handle_mock = AsyncMock()
    handle_mock.content_frame = AsyncMock(return_value=child)
    locator_mock = MagicMock()
    locator_mock.owner.return_value.element_handle = AsyncMock(return_value=handle_mock)
    s.page.frame_locator = MagicMock(return_value=locator_mock)  # type: ignore[attr-defined]

    result = await s.switch_frame(selector="iframe#widget")
    assert s.active_frame is child
    assert result["name"] == "inner"


# ---------------------------------------------------------------------------
# switch_frame raises on multiple/no args
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_switch_frame_raises_on_no_args(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    with pytest.raises(ValueError, match="Exactly one"):
        await s.switch_frame()


@pytest.mark.anyio
async def test_switch_frame_raises_on_multiple_args(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    with pytest.raises(ValueError, match="Exactly one"):
        await s.switch_frame(name="a", url_pattern="b")
