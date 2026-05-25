# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
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

    def on(self, event: str, handler: object) -> None:
        pass

    async def route(self, pattern: str, handler: object) -> None:
        self._routes[pattern] = handler

    async def unroute(self, pattern: str, handler: object) -> None:
        self._routes.pop(pattern, None)

    async def set_input_files(self, selector: str, paths: list[str]) -> None:
        self.set_input_files_calls.append((selector, paths))


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
