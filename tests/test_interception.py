from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.recorder import Recorder
from octowright.session import BrowserSession


# ---------------------------------------------------------------------------
# Minimal stubs — no real browser launched
# ---------------------------------------------------------------------------


class FakeRoute:
    def __init__(self) -> None:
        self.fulfilled: dict[str, Any] | None = None

    async def fulfill(self, **kwargs: Any) -> None:
        self.fulfilled = kwargs


class FakeDialog:
    def __init__(self, dtype: str = "alert", message: str = "hi") -> None:
        self.type = dtype
        self.message = message
        self.accepted: bool | None = None
        self.dismissed: bool | None = None
        self.accept_text: str | None = None

    async def accept(self, text: str = "") -> None:
        self.accepted = True
        self.accept_text = text

    async def dismiss(self) -> None:
        self.dismissed = True


class FakePage:
    def __init__(self) -> None:
        self._routes: dict[str, Any] = {}
        self._listeners: dict[str, list[Any]] = {}
        self.set_input_files_calls: list[tuple[str, list[str]]] = []

    def on(self, event: str, handler: Any) -> None:
        self._listeners.setdefault(event, []).append(handler)

    async def route(self, pattern: str, handler: Any) -> None:
        self._routes[pattern] = handler

    async def unroute(self, pattern: str, handler: Any) -> None:
        self._routes.pop(pattern, None)

    async def set_input_files(self, selector: str, paths: list[str]) -> None:
        self.set_input_files_calls.append((selector, paths))


def _make_session(tmp_path: Path) -> BrowserSession:
    """Build a BrowserSession with a FakePage and real Recorder writing to tmp_path."""
    log_path = tmp_path / "test.jsonl"
    recorder = Recorder(log_path)
    fake_page = FakePage()
    session = BrowserSession(
        instance_id="test123",
        kind="chromium",
        label=None,
        url="https://example.com",
        browser=None,  # type: ignore[arg-type]
        context=MagicMock(),
        page=fake_page,  # type: ignore[arg-type]
        recorder=recorder,
        log_path=log_path,
    )
    return session


# ---------------------------------------------------------------------------
# set_dialog_policy
# ---------------------------------------------------------------------------


def test_set_dialog_policy_rejects_invalid(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    with pytest.raises(ValueError, match="policy must be accept"):
        s.set_dialog_policy("yolo")


def test_set_dialog_policy_updates_state(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    result = s.set_dialog_policy("accept", prompt_text="my text")
    assert result == {"ok": True, "policy": "accept", "prompt_text": "my text"}
    assert s._dialog_policy == "accept"
    assert s._dialog_prompt_text == "my text"


def test_set_dialog_policy_records_action(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    s.set_dialog_policy("manual")
    log = (tmp_path / "test.jsonl").read_text()
    assert "set_dialog_policy" in log
    assert "manual" in log


# ---------------------------------------------------------------------------
# _handle_dialog
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_handle_dialog_accept_prompt_uses_prompt_text(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    s._dialog_policy = "accept"
    s._dialog_prompt_text = "my answer"

    dialog = FakeDialog(dtype="prompt", message="What?")
    s._handle_dialog(dialog)

    # give the spawned task a chance to run
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert dialog.accepted is True
    assert dialog.accept_text == "my answer"
    assert dialog.dismissed is None


@pytest.mark.anyio
async def test_handle_dialog_accept_non_prompt(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    s._dialog_policy = "accept"

    dialog = FakeDialog(dtype="alert", message="Alert!")
    s._handle_dialog(dialog)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert dialog.accepted is True
    assert dialog.dismissed is None


@pytest.mark.anyio
async def test_handle_dialog_dismiss(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    s._dialog_policy = "dismiss"

    dialog = FakeDialog(dtype="confirm", message="Sure?")
    s._handle_dialog(dialog)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert dialog.dismissed is True
    assert dialog.accepted is None


@pytest.mark.anyio
async def test_handle_dialog_manual_does_nothing(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    s._dialog_policy = "manual"

    dialog = FakeDialog(dtype="alert", message="Hey")
    s._handle_dialog(dialog)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert dialog.accepted is None
    assert dialog.dismissed is None


# ---------------------------------------------------------------------------
# mock_route / unmock_route
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mock_route_installs_handler(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    result = await s.mock_route("**/api/data", status=200, body='{"ok":true}')

    assert result["ok"] is True
    assert result["pattern"] == "**/api/data"
    assert result["status"] == 200
    assert "**/api/data" in s._active_routes
    assert "**/api/data" in s.page._routes  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_mock_route_records_action(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    await s.mock_route("**/x", status=404)
    log = (tmp_path / "test.jsonl").read_text()
    assert "mock_route" in log
    assert "**/x" in log


@pytest.mark.anyio
async def test_unmock_route_removes_handler(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    await s.mock_route("**/api/data", status=200)
    result = await s.unmock_route("**/api/data")

    assert result["ok"] is True
    assert "**/api/data" not in s._active_routes
    assert "**/api/data" not in s.page._routes  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_unmock_route_unknown_raises(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    with pytest.raises(KeyError, match="no active mock"):
        await s.unmock_route("**/nonexistent")


@pytest.mark.anyio
async def test_mock_route_replacing_unroutes_old_handler(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    await s.mock_route("**/api/data", status=200)
    first_handler = s._active_routes["**/api/data"]

    # Install a replacement — should unroute the old one and install a new handler.
    await s.mock_route("**/api/data", status=404)
    second_handler = s._active_routes["**/api/data"]

    assert first_handler is not second_handler
    # The route dict should now contain only the new handler.
    assert s.page._routes["**/api/data"] is second_handler  # type: ignore[attr-defined]
