# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.recorder import Recorder
from octowright.session import BrowserSession

# ---------------------------------------------------------------------------
# Fake download object
# ---------------------------------------------------------------------------


class FakeDownload:
    def __init__(self, url: str = "https://octowright.com/file.csv", filename: str = "file.csv") -> None:
        self.url = url
        self.suggested_filename = filename
        self._saved_to: str | None = None
        self.save_as = AsyncMock(side_effect=self._do_save)

    async def _do_save(self, path: str) -> None:
        self._saved_to = path
        # Mirror Playwright's save_as, which os.makedirs(dirname) before writing.
        # That dir creation is precisely what turns a "NNN-.." prefix component
        # into a real, traversable directory — so the fake must do it too or it
        # would mask the very escape this guards against.
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"data")


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


class FakePage:
    def __init__(self) -> None:
        self.url = "https://octowright.com"
        self.frames: list[Any] = []
        self._handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers[event] = handler


def _make_session(tmp_path: Path) -> BrowserSession:
    log_path = tmp_path / "test.jsonl"
    recorder = Recorder(log_path)
    page = FakePage()
    return BrowserSession(
        instance_id="dl-test-abc",
        kind="chromium",
        label=None,
        url="https://octowright.com",
        browser=None,  # type: ignore[arg-type]
        context=MagicMock(),
        page=page,  # type: ignore[arg-type]
        recorder=recorder,
        log_path=log_path,
    )


async def _drain(session: BrowserSession) -> None:
    """Let ``_handle_download``'s background save task run to completion.

    ``save_download`` is now ``async with session.operation("download_save")``
    (Task 6), so it goes through the gate's FIFO admission path rather than
    completing within whatever event-loop turn a single ``sleep(0)`` covers.
    """
    tasks = list(session._bg_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def wait_for_queue_depth(gate: Any, depth: int) -> None:
    async with asyncio.timeout(1):
        while gate.snapshot()["queue_depth"] != depth:
            await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# _handle_download — saves file, appends record
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_handle_download_appends_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path))
    # Reload defaults so RECORDINGS_DIR picks up the monkeypatched env var
    import octowright.defaults as defs

    monkeypatch.setattr(defs, "RECORDINGS_DIR", tmp_path)

    s = _make_session(tmp_path)
    dl = FakeDownload(url="https://octowright.com/report.csv", filename="report.csv")

    s._handle_download(dl)
    # _handle_download schedules a task; let it run
    await _drain(s)

    assert len(s.downloads) == 1
    rec = s.downloads[0]
    assert rec["url"] == "https://octowright.com/report.csv"
    assert rec["suggested_filename"] == "report.csv"
    assert "report.csv" in rec["path"]
    assert "timestamp" in rec


@pytest.mark.anyio
async def test_handle_download_saves_to_downloads_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.defaults as defs

    monkeypatch.setattr(defs, "RECORDINGS_DIR", tmp_path)

    s = _make_session(tmp_path)
    dl = FakeDownload(filename="data.json")

    s._handle_download(dl)
    await _drain(s)

    assert len(s.downloads) == 1
    saved_path = Path(s.downloads[0]["path"])
    assert saved_path.exists()
    assert saved_path.parent == tmp_path / "downloads" / s.instance_id


@pytest.mark.anyio
async def test_download_anchors_on_session_recordings_root_not_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-pool routing: downloads land beside the session's own recording
    (``log_path.parent`` = the owning pool's recordings_dir), NOT the
    process-global RECORDINGS_DIR. Proves a pool given a custom recordings_dir
    keeps its downloads under that root."""
    import octowright.defaults as defs

    global_root = tmp_path / "global"
    pool_root = tmp_path / "pool-b"
    pool_root.mkdir()
    # Global root stays the default; the session lives under a DIFFERENT root.
    monkeypatch.setattr(defs, "RECORDINGS_DIR", global_root)
    s = _make_session(pool_root)  # log_path = pool_root / "test.jsonl"

    s._handle_download(FakeDownload(filename="report.csv"))
    await _drain(s)

    saved = Path(s.downloads[0]["path"]).resolve()
    assert saved.is_relative_to((pool_root / "downloads" / s.instance_id).resolve())
    # Nothing leaked into the process-global root.
    assert not (global_root / "downloads").exists()


@pytest.mark.anyio
async def test_handle_download_increments_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.defaults as defs

    monkeypatch.setattr(defs, "RECORDINGS_DIR", tmp_path)

    s = _make_session(tmp_path)

    s._handle_download(FakeDownload(filename="a.csv"))
    await _drain(s)
    s._handle_download(FakeDownload(filename="b.csv"))
    await _drain(s)

    assert len(s.downloads) == 2
    assert Path(s.downloads[0]["path"]).name.startswith("000-")
    assert Path(s.downloads[1]["path"]).name.startswith("001-")


# ---------------------------------------------------------------------------
# path containment — remote-controlled suggested_filename
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_malicious_suggested_filename_cannot_escape_downloads_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suggested_filename comes from the remote page's Content-Disposition.
    A ``../``-laden value must not redirect the write outside the per-session
    downloads dir — the ``NNN-`` prefix only neutralizes the first segment."""
    import octowright.defaults as defs

    monkeypatch.setattr(defs, "RECORDINGS_DIR", tmp_path)
    s = _make_session(tmp_path)
    dl = FakeDownload(url="https://evil/x", filename="../../../../pwned.txt")

    s._handle_download(dl)
    await _drain(s)

    assert len(s.downloads) == 1, "download was dropped instead of contained"
    saved = Path(s.downloads[0]["path"]).resolve()
    contain = (tmp_path / "downloads" / s.instance_id).resolve()
    assert saved.is_relative_to(contain), f"escaped containment: {saved}"
    assert ".." not in saved.parts
    # The sanitized on-disk name keeps the basename; the record preserves the
    # original suggested value for fidelity.
    assert saved.name.endswith("pwned.txt")
    assert s.downloads[0]["suggested_filename"] == "../../../../pwned.txt"
    # And nothing was written outside the recordings root.
    assert not (tmp_path / "pwned.txt").exists()


@pytest.mark.anyio
async def test_dotdot_only_filename_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A filename that sanitizes to nothing (``..``) still produces a safe name."""
    import octowright.defaults as defs

    monkeypatch.setattr(defs, "RECORDINGS_DIR", tmp_path)
    s = _make_session(tmp_path)
    s._handle_download(FakeDownload(filename=".."))
    await _drain(s)
    assert len(s.downloads) == 1
    saved = Path(s.downloads[0]["path"]).resolve()
    assert saved.is_relative_to((tmp_path / "downloads" / s.instance_id).resolve())


# ---------------------------------------------------------------------------
# list_downloads
# ---------------------------------------------------------------------------


def test_list_downloads_empty(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    assert s.list_downloads() == []


@pytest.mark.anyio
async def test_list_downloads_returns_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.defaults as defs

    monkeypatch.setattr(defs, "RECORDINGS_DIR", tmp_path)

    s = _make_session(tmp_path)
    s._handle_download(FakeDownload(filename="x.txt"))
    await _drain(s)

    result = s.list_downloads()
    assert len(result) == 1
    # Mutating the returned list should not affect internal state
    result.clear()
    assert len(s.downloads) == 1


# ---------------------------------------------------------------------------
# wait_for_download
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_download_ignores_prior_and_waits_for_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import octowright.defaults as defs

    monkeypatch.setattr(defs, "RECORDINGS_DIR", tmp_path)

    s = _make_session(tmp_path)
    # Prior download must NOT satisfy the wait — the contract is "next".
    s._handle_download(FakeDownload(filename="pre.csv"))
    await _drain(s)

    async def _trigger_after_delay() -> None:
        await asyncio.sleep(0.05)
        s._handle_download(FakeDownload(filename="next.csv"))

    task = asyncio.create_task(_trigger_after_delay())
    rec = await s.wait_for_download(timeout_ms=2000)
    await task
    assert rec["suggested_filename"] == "next.csv"


@pytest.mark.anyio
async def test_wait_for_download_blocks_then_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.defaults as defs

    monkeypatch.setattr(defs, "RECORDINGS_DIR", tmp_path)

    s = _make_session(tmp_path)

    async def _trigger_after_delay() -> None:
        await asyncio.sleep(0.05)
        s._handle_download(FakeDownload(filename="late.csv"))

    task = asyncio.create_task(_trigger_after_delay())
    rec = await s.wait_for_download(timeout_ms=2000)
    await task
    assert rec["suggested_filename"] == "late.csv"


@pytest.mark.anyio
async def test_wait_for_download_raises_timeout(tmp_path: Path) -> None:
    s = _make_session(tmp_path)
    with pytest.raises(TimeoutError, match="no download within"):
        await s.wait_for_download(timeout_ms=50)


# ---------------------------------------------------------------------------
# operation-gate serialization (Task 6)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_download_save_queues_behind_an_active_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``save_download`` runs ``async with session.operation("download_save")``,
    so the background task ``_handle_download`` schedules must queue behind an
    already-active operation rather than racing it -- e.g. a page that
    triggers a download mid-click must not have the save land while the click
    is still resolving."""
    import octowright.defaults as defs

    monkeypatch.setattr(defs, "RECORDINGS_DIR", tmp_path)
    s = _make_session(tmp_path)
    dl = FakeDownload(filename="report.csv")

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _hold() -> None:
        async with s.operation("browser_click"):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(_hold())
    await entered.wait()

    s._handle_download(dl)
    await wait_for_queue_depth(s._operation_gate, 1)
    dl.save_as.assert_not_awaited()

    release.set()
    await holder
    await _drain(s)
    assert len(s.downloads) == 1
    assert s.downloads[0]["suggested_filename"] == "report.csv"


@pytest.mark.anyio
async def test_wait_for_download_is_ungated_and_stays_concurrent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``wait_for_download`` waits on Octowright's own event/list, not the
    operation gate -- it must keep waiting immediately even while another
    operation is active, and must not itself add to the queue depth. Gating
    it would deadlock any caller pairing it with an action that queues behind
    the same active operation before triggering the download."""
    import octowright.defaults as defs

    monkeypatch.setattr(defs, "RECORDINGS_DIR", tmp_path)
    s = _make_session(tmp_path)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _hold() -> None:
        async with s.operation("browser_click"):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(_hold())
    await entered.wait()

    wait_task = asyncio.create_task(s.wait_for_download(timeout_ms=2000))
    await asyncio.sleep(0)
    assert s.operation_snapshot()["queue_depth"] == 0

    s._handle_download(FakeDownload(filename="late.csv"))
    await wait_for_queue_depth(s._operation_gate, 1)

    release.set()
    await holder

    rec = await asyncio.wait_for(wait_task, timeout=2.0)
    assert rec["suggested_filename"] == "late.csv"
