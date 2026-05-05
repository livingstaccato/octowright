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

from octowright.browser_pool import BrowserPool


class FakeRecorder:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.closed = False

    def record(self, _action: str, **_fields: object) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakePage:
    video = None
    url = "about:blank"

    def __init__(self) -> None:
        self.main_frame = SimpleNamespace(url="about:blank")
        self.events: dict[str, Any] = {}

    async def goto(self, url: str) -> None:
        self.url = url
        self.main_frame.url = url

    def is_closed(self) -> bool:
        return False

    def on(self, *_args: object) -> None:
        event, handler = _args
        self.events[str(event)] = handler


class FakeContext:
    def __init__(self) -> None:
        self.page = FakePage()
        self.pages: list[Any] = []
        self.closed = False
        self.tracing = SimpleNamespace(start=self._tracing_start)
        self.events: dict[str, Any] = {}

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True

    async def add_init_script(self, *, script: str) -> None:
        return None

    async def _tracing_start(self, **_kwargs: object) -> None:
        return None

    def on(self, *_args: object) -> None:
        event, handler = _args
        self.events[str(event)] = handler


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False
        self.events: dict[str, Any] = {}

    async def new_context(self, **_kwargs: object) -> FakeContext:
        return self.context

    async def close(self) -> None:
        self.closed = True

    def on(self, *_args: object) -> None:
        event, handler = _args
        self.events[str(event)] = handler


class FakeBrowserType:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser

    async def launch(self, **_kwargs: object) -> FakeBrowser:
        return self.browser

    async def launch_persistent_context(self, _user_data_dir: str, **_kwargs: object) -> FakeContext:
        return self.browser.context


@pytest.fixture
def isolated_pool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[BrowserPool, Path]:
    import octowright.browser_pool.pool as _pool
    from octowright import defaults as _defaults
    from octowright import session_manifest as _manifest
    from octowright.session.core import BrowserSession

    recordings = tmp_path / "recordings"
    recordings.mkdir()
    manifest_path = recordings / "session-manifest.json"

    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", recordings)
    monkeypatch.setattr(_defaults, "SESSION_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", recordings)
    monkeypatch.setattr(_manifest, "SESSION_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(_pool, "Recorder", FakeRecorder)
    monkeypatch.setattr(BrowserSession, "_schedule_markdown_capture", lambda self: None)

    context = FakeContext()
    browser = FakeBrowser(context)
    pool = BrowserPool()
    pool._pw = SimpleNamespace(chromium=FakeBrowserType(browser))  # type: ignore[assignment]
    return pool, manifest_path


@pytest.mark.asyncio
async def test_launch_writes_session_manifest(isolated_pool: tuple[BrowserPool, Path]) -> None:
    pool, manifest_path = isolated_pool

    result = await pool.launch(kind="chromium", url="https://example.test", headed=False, label="ops")

    body = json.loads(manifest_path.read_text())
    entry = body["sessions"][result["instance_id"]]
    assert entry["session_id"] == result["instance_id"]
    assert entry["kind"] == "chromium"
    assert entry["label"] == "ops"
    assert entry["profile"] == "ops"
    assert entry["user_data_dir"]
    assert entry["log_path"] == result["log_path"]
    assert entry["state"] == "open"
    assert isinstance(entry["launched_at"], str)
    assert isinstance(entry["daemon_pid"], int)
    assert "browser_pid" not in entry


@pytest.mark.asyncio
async def test_close_clears_session_manifest(isolated_pool: tuple[BrowserPool, Path]) -> None:
    pool, manifest_path = isolated_pool
    result = await pool.launch(kind="chromium", url="https://example.test", headed=False, ephemeral=True)

    await pool.close(result["instance_id"])

    body = json.loads(manifest_path.read_text())
    assert body["sessions"] == {}


@pytest.mark.asyncio
async def test_external_context_close_clears_session_manifest(isolated_pool: tuple[BrowserPool, Path]) -> None:
    pool, manifest_path = isolated_pool
    result = await pool.launch(kind="chromium", url="https://example.test", headed=False, ephemeral=True)
    session = pool.get(result["instance_id"])

    session.context.events["close"]()

    body = json.loads(manifest_path.read_text())
    assert body["sessions"] == {}
    assert not pool.has_session(result["instance_id"])


def test_stale_manifest_entries_can_be_diagnosed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from octowright import defaults as _defaults
    from octowright import session_manifest as _manifest

    manifest_path = tmp_path / "recordings" / "session-manifest.json"
    monkeypatch.setattr(_defaults, "SESSION_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(_manifest, "SESSION_MANIFEST_PATH", manifest_path)
    _manifest.record_launch(
        session_id="stale123",
        kind="firefox",
        label="stale-label",
        profile=None,
        user_data_dir=None,
        log_path=tmp_path / "recordings" / "stale.jsonl",
    )

    stale = _manifest.stale_entries(live_session_ids=set())

    assert [entry["session_id"] for entry in stale] == ["stale123"]
    assert stale[0]["reason"] == "manifest entry is not present in the live browser pool"
