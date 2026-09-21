# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright import defaults
from octowright.recorder import Recorder
from octowright.session import BrowserSession

# ---------------------------------------------------------------------------
# Minimal fake page for upload tests
# ---------------------------------------------------------------------------


class FakePage:
    def __init__(self) -> None:
        self.set_input_files_calls: list[tuple[str, list[str]]] = []
        self._routes: dict = {}
        self.events: list[str] = []
        self.locator_calls: list[str] = []
        self.expect_file_chooser_calls: list[int] = []
        self.locator_result = _FakeLocator(self.events)
        self.chooser = _FakeFileChooser(self.events)

    def on(self, event: str, handler: object) -> None:
        pass

    async def route(self, pattern: str, handler: object) -> None:
        self._routes[pattern] = handler

    async def unroute(self, pattern: str, handler: object) -> None:
        self._routes.pop(pattern, None)

    async def set_input_files(self, selector: str, paths: list[str]) -> None:
        self.set_input_files_calls.append((selector, paths))

    def locator(self, selector: str) -> _FakeLocator:
        self.locator_calls.append(selector)
        return self.locator_result

    def expect_file_chooser(self, *, timeout: int) -> _FakeChooserContext:
        self.events.append("expect_file_chooser")
        self.expect_file_chooser_calls.append(timeout)
        return _FakeChooserContext(self.events, self.chooser)


class _FakeLocator:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.click_calls: list[int] = []

    async def click(self, *, timeout: int) -> None:
        self.events.append("click")
        self.click_calls.append(timeout)


class _FakeFileChooser:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.set_files_calls: list[tuple[list[str], int]] = []

    async def set_files(self, paths: list[str], *, timeout: int) -> None:
        self.events.append("set_files")
        self.set_files_calls.append((paths, timeout))


class _FakeChooserContext:
    def __init__(self, events: list[str], chooser: _FakeFileChooser) -> None:
        self.events = events
        self.chooser = chooser

    async def __aenter__(self) -> _FakeChooserContext:
        self.events.append("listener_armed")
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    @property
    async def value(self) -> _FakeFileChooser:
        self.events.append("chooser_value")
        return self.chooser


def _make_session(tmp_path: Path) -> BrowserSession:
    log_path = tmp_path / "test.jsonl"
    recorder = Recorder(log_path)
    fake_page = FakePage()
    return BrowserSession(
        instance_id="upload-test",
        kind="chromium",
        label=None,
        url="https://octowright.com",
        browser=None,  # type: ignore[arg-type]
        context=MagicMock(),
        page=fake_page,  # type: ignore[arg-type]
        recorder=recorder,
        log_path=log_path,
    )


# ---------------------------------------------------------------------------
# set_input_files tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_input_files_calls_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point the upload allowlist at tmp_path so the session validator accepts
    # paths created here without us tripping the real staging-dir allowlist.
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", tmp_path)
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
    s = _make_session(tmp_path)
    f1 = tmp_path / "file1.txt"
    f2 = tmp_path / "file2.png"
    f1.write_text("a")
    f2.write_bytes(b"\x89PNG")
    paths = [str(f1), str(f2)]
    result = await s.set_input_files("#file-input", paths)

    assert result == {"ok": True, "selector": "#file-input", "paths": paths}
    assert s.page.set_input_files_calls == [("#file-input", paths)]  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_set_input_files_records_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", tmp_path)
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
    s = _make_session(tmp_path)
    upload = tmp_path / "upload.csv"
    upload.write_text("col1,col2\n")
    await s.set_input_files("input[type=file]", [str(upload)])
    log = (tmp_path / "test.jsonl").read_text()
    assert "set_input_files" in log
    assert "upload.csv" in log


# ---------------------------------------------------------------------------
# atomic upload_files tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_upload_files_arms_listener_before_click_and_records_only_atomic_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", tmp_path)
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
    session = _make_session(tmp_path)
    upload = tmp_path / "upload.csv"
    upload.write_text("col1,col2\n")

    result = await session.upload_files(paths=[str(upload)], selector="#upload", timeout_ms=321)

    page = session.page
    assert page.events == [  # type: ignore[attr-defined]
        "expect_file_chooser",
        "listener_armed",
        "click",
        "chooser_value",
        "set_files",
    ]
    assert page.locator_calls == ["#upload"]  # type: ignore[attr-defined]
    assert page.expect_file_chooser_calls == [321]  # type: ignore[attr-defined]
    assert page.locator_result.click_calls == [321]  # type: ignore[attr-defined]
    assert page.chooser.set_files_calls == [([str(upload)], 321)]  # type: ignore[attr-defined]
    assert result == {"ok": True, "paths": [str(upload)], "selector": "#upload"}

    rows = [json.loads(line) for line in (tmp_path / "test.jsonl").read_text().splitlines()]
    actions = [row["action"] for row in rows]
    assert actions.count("upload_files") == 1
    assert not ({"click", "click_by", "set_input_files"} & set(actions))
    assert rows[-1] == {
        "ts": rows[-1]["ts"],
        "action": "upload_files",
        "paths": [str(upload)],
        "selector": "#upload",
    }
