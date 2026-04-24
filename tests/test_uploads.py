from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from octowright.recorder import Recorder
from octowright.session import BrowserSession


# ---------------------------------------------------------------------------
# Minimal fake page for upload tests
# ---------------------------------------------------------------------------


class FakePage:
    def __init__(self) -> None:
        self.set_input_files_calls: list[tuple[str, list[str]]] = []
        self._routes: dict = {}

    def on(self, event: str, handler: object) -> None:  # noqa: ARG002
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
        url="https://example.com",
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
async def test_set_input_files_calls_page(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    paths = ["/tmp/file1.txt", "/tmp/file2.png"]
    result = await s.set_input_files("#file-input", paths)

    assert result == {"ok": True, "selector": "#file-input", "paths": paths}
    assert s.page.set_input_files_calls == [("#file-input", paths)]  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_set_input_files_records_action(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    await s.set_input_files("input[type=file]", ["/tmp/upload.csv"])
    log = (tmp_path / "test.jsonl").read_text()
    assert "set_input_files" in log
    assert "upload.csv" in log
