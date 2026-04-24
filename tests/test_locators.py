from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright.session import BrowserSession

# ---------------------------------------------------------------------------
# Fake Locator
# ---------------------------------------------------------------------------


@dataclass
class FakeLocator:
    """Records calls to Playwright Locator methods."""

    _clicks: list[dict[str, Any]] = field(default_factory=list)
    _fills: list[dict[str, Any]] = field(default_factory=list)
    _waits: list[dict[str, Any]] = field(default_factory=list)
    _inner_text_value: str = "Click me"

    async def click(self, timeout: int = 5000) -> None:
        self._clicks.append({"timeout": timeout})

    async def fill(self, value: str, timeout: int = 5000) -> None:
        self._fills.append({"value": value, "timeout": timeout})

    async def wait_for(self, timeout: int = 5000) -> None:
        self._waits.append({"timeout": timeout})

    async def inner_text(self) -> str:
        return self._inner_text_value


# ---------------------------------------------------------------------------
# Fake Page
# ---------------------------------------------------------------------------


class FakePage:
    """Minimal stub that records get_by_* calls and returns a FakeLocator."""

    def __init__(self) -> None:
        self._locator = FakeLocator()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, method: str, *args: Any, **kwargs: Any) -> FakeLocator:
        self.calls.append((method, args, kwargs))
        return self._locator

    def get_by_role(self, role: str, **kwargs: Any) -> FakeLocator:
        return self._record("get_by_role", role, **kwargs)

    def get_by_label(self, text: str) -> FakeLocator:
        return self._record("get_by_label", text)

    def get_by_text(self, text: str) -> FakeLocator:
        return self._record("get_by_text", text)

    def get_by_test_id(self, test_id: str) -> FakeLocator:
        return self._record("get_by_test_id", test_id)


# ---------------------------------------------------------------------------
# Fake Recorder
# ---------------------------------------------------------------------------


class FakeRecorder:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, dict[str, Any]]] = []

    def record(self, action: str, **kwargs: Any) -> None:
        self.recorded.append((action, kwargs))

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


def _make_session(page: FakePage, tmp_path: Path) -> BrowserSession:
    recorder = FakeRecorder()
    log_path = tmp_path / "rec.jsonl"
    # Build a minimal BrowserSession — fields not used by locator methods are mocked.
    session = BrowserSession.__new__(BrowserSession)
    session.instance_id = "test-id"
    session.kind = "webkit"
    session.label = None
    session.url = "about:blank"
    session.browser = None
    session.context = MagicMock()
    session.page = page  # type: ignore[assignment]
    session.recorder = recorder  # type: ignore[assignment]
    session.log_path = log_path
    session.profile = None
    session.stabilize = False
    session.trace = False
    session.console = []
    session.video_path = None
    session.trace_path = None
    session._video = None
    session.pages = [page]  # type: ignore[list-item]
    session._dialog_policy = "dismiss"
    session._dialog_prompt_text = None
    session._active_routes = {}
    session.active_frame = None
    session.downloads = []
    session._pending_download_events = []
    return session


# ---------------------------------------------------------------------------
# _locator validation tests
# ---------------------------------------------------------------------------


def test_locator_no_finders_raises(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    with pytest.raises(ValueError, match="exactly one"):
        session._locator()


def test_locator_two_finders_raises(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    with pytest.raises(ValueError, match="exactly one"):
        session._locator(role="button", label="Email")


def test_locator_role_with_name(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    session._locator(role="button", role_name="Submit")
    assert len(page.calls) == 1
    method, args, kwargs = page.calls[0]
    assert method == "get_by_role"
    assert args == ("button",)
    assert kwargs == {"name": "Submit", "exact": False}


def test_locator_role_without_name_passes_no_kwargs(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    session._locator(role="button")
    assert len(page.calls) == 1
    method, args, kwargs = page.calls[0]
    assert method == "get_by_role"
    assert args == ("button",)
    # No 'name' key when role_name is omitted
    assert "name" not in kwargs


def test_locator_label(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    session._locator(label="Email")
    assert page.calls == [("get_by_label", ("Email",), {})]


def test_locator_text(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    session._locator(text="Hi")
    assert page.calls == [("get_by_text", ("Hi",), {})]


def test_locator_test_id(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    session._locator(test_id="login")
    assert page.calls == [("get_by_test_id", ("login",), {})]


# ---------------------------------------------------------------------------
# click_by
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_click_by_calls_locator_click(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    result = await session.click_by(role="button", role_name="Submit", timeout_ms=3000)
    assert result == {"ok": True}
    assert len(page._locator._clicks) == 1
    assert page._locator._clicks[0]["timeout"] == 3000
    # Recorder captured the action
    assert session.recorder.recorded[-1][0] == "click_by"  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_click_by_uses_default_timeout(tmp_path: Path) -> None:
    from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS

    page = FakePage()
    session = _make_session(page, tmp_path)
    await session.click_by(text="Click me")
    assert page._locator._clicks[0]["timeout"] == DEFAULT_ACTION_TIMEOUT_MS


# ---------------------------------------------------------------------------
# fill_by
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fill_by_calls_locator_fill(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    result = await session.fill_by("me@example.com", label="Email", timeout_ms=2000)
    assert result == {"ok": True}
    assert len(page._locator._fills) == 1
    assert page._locator._fills[0] == {"value": "me@example.com", "timeout": 2000}
    assert session.recorder.recorded[-1][0] == "fill_by"  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_fill_by_records_value(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    await session.fill_by("hunter2", test_id="password-input")
    action, kwargs = session.recorder.recorded[-1]  # type: ignore[union-attr]
    assert action == "fill_by"
    assert kwargs["value"] == "hunter2"
    assert kwargs["test_id"] == "password-input"


# ---------------------------------------------------------------------------
# get_text_by
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_text_by_waits_and_returns_inner_text(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    result = await session.get_text_by(text="Click me", timeout_ms=4000)
    assert result == {"ok": True, "text": "Click me"}
    assert len(page._locator._waits) == 1
    assert page._locator._waits[0]["timeout"] == 4000


@pytest.mark.anyio
async def test_get_text_by_records_action(tmp_path: Path) -> None:
    page = FakePage()
    session = _make_session(page, tmp_path)
    await session.get_text_by(role="heading", role_name="Welcome")
    action, kwargs = session.recorder.recorded[-1]  # type: ignore[union-attr]
    assert action == "get_text_by"
    assert kwargs["role"] == "heading"
    assert kwargs["result"] == "Click me"


# ---------------------------------------------------------------------------
# _target() routing: active_frame is used when set
# ---------------------------------------------------------------------------


def test_locator_routes_through_active_frame(tmp_path: Path) -> None:
    page = FakePage()
    frame = FakePage()
    session = _make_session(page, tmp_path)
    session.active_frame = frame  # type: ignore[assignment]
    session._locator(label="Email")
    # Frame got the call, not the page
    assert len(frame.calls) == 1
    assert len(page.calls) == 0
