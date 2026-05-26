# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.recorder import Recorder
from octowright.session.core import BrowserSession


class _FakeEvented:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event: str, callback: Any) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def emit(self, event: str, *args: Any) -> None:
        for cb in self.handlers.get(event, []):
            cb(*args)


class _FakeFrame:
    def __init__(self, payload: Any, *, is_binary: bool = False) -> None:
        self.payload = payload
        self.is_binary = is_binary


class _FakeWebSocket(_FakeEvented):
    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url


class _FakeContext:
    async def close(self) -> None:
        return None


class _FakeBrowser:
    async def close(self) -> None:
        return None


@pytest.mark.anyio
async def test_websocket_events_are_recorded_to_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "websocket.jsonl"
    recorder = Recorder(log_path)
    session = BrowserSession(
        instance_id="ws-cache",
        kind="chromium",
        label="ws",
        url="https://octowright.com",
        browser=_FakeBrowser(),
        context=_FakeContext(),
        page=SimpleNamespace(url="https://octowright.com", content=None),
        recorder=recorder,
        log_path=log_path,
    )

    ws = _FakeWebSocket("wss://example")
    session._handle_websocket(ws)
    ws.emit("framesent", _FakeFrame("hello world"))
    ws.emit("framereceived", _FakeFrame(b"binary", is_binary=True))
    ws.emit("socketerror", RuntimeError("boom"))
    ws.emit("close")

    actions = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    names = [a.get("action") for a in actions]
    assert "websocket_opened" in names
    assert "websocket_framesent" in names
    assert "websocket_framereceived" in names
    assert "websocket_error" in names
    assert "websocket_closed" in names

    received = next(entry for entry in actions if entry["action"] == "websocket_framereceived")
    assert received["payload_preview"] == "[binary payload hidden: 6 bytes]"
    assert received["is_binary"] is True


@pytest.mark.anyio
async def test_websocket_events_hide_binary_payload_without_is_binary_flag(tmp_path: Path) -> None:
    log_path = tmp_path / "websocket.jsonl"
    recorder = Recorder(log_path)
    session = BrowserSession(
        instance_id="ws-cache-no-flag",
        kind="chromium",
        label="ws",
        url="https://octowright.com",
        browser=_FakeBrowser(),
        context=_FakeContext(),
        page=SimpleNamespace(url="https://octowright.com", content=None),
        recorder=recorder,
        log_path=log_path,
    )

    ws = _FakeWebSocket("wss://example")
    session._handle_websocket(ws)
    ws.emit("framereceived", _FakeFrame(b"secret"))

    actions = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    received = next(entry for entry in actions if entry["action"] == "websocket_framereceived")
    assert received["payload_preview"] == "[binary payload hidden: 6 bytes]"
    assert received["is_binary"] is True


@pytest.mark.anyio
async def test_websocket_events_hide_binary_payload_text_form(tmp_path: Path) -> None:
    log_path = tmp_path / "websocket-text.jsonl"
    recorder = Recorder(log_path)
    session = BrowserSession(
        instance_id="ws-cache-text",
        kind="chromium",
        label="ws",
        url="https://octowright.com",
        browser=_FakeBrowser(),
        context=_FakeContext(),
        page=SimpleNamespace(url="https://octowright.com", content=None),
        recorder=recorder,
        log_path=log_path,
    )

    ws = _FakeWebSocket("wss://example")
    session._handle_websocket(ws)
    ws.emit("framereceived", _FakeFrame("b'secret'"))

    actions = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    received = next(entry for entry in actions if entry["action"] == "websocket_framereceived")
    assert received["payload_preview"] == "[binary payload hidden: 6 bytes]"
    assert received["is_binary"] is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        bytearray(b"binary"),
        memoryview(b"binary"),
    ],
)
async def test_websocket_binary_like_views_are_hidden(tmp_path: Path, payload: object) -> None:
    log_path = tmp_path / "websocket-binaryview.jsonl"
    recorder = Recorder(log_path)
    session = BrowserSession(
        instance_id="ws-cache-view",
        kind="chromium",
        label="ws",
        url="https://octowright.com",
        browser=_FakeBrowser(),
        context=_FakeContext(),
        page=SimpleNamespace(url="https://octowright.com", content=None),
        recorder=recorder,
        log_path=log_path,
    )

    ws = _FakeWebSocket("wss://example")
    session._handle_websocket(ws)
    ws.emit("framereceived", _FakeFrame(payload))

    actions = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    received = next(entry for entry in actions if entry["action"] == "websocket_framereceived")
    assert received["payload_preview"] == "[binary payload hidden: 6 bytes]"
    assert received["is_binary"] is True
