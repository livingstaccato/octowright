# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The launch path prunes a Chromium profile's download rows BEFORE Chromium opens it.

The pruner itself is covered by ``test_download_history.py``; this pins where it
runs. The ordering is the whole point: Chromium reads ``History`` at startup, so
a prune after ``launch_persistent_context`` would leave the crashing row in
place for exactly the run it was meant to protect.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from octowright import personas
from octowright.browser_pool import launch_helpers


def _seed(pdir: Path) -> Path:
    leaf = pdir / "Default"
    leaf.mkdir(parents=True, exist_ok=True)
    db = leaf / "History"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY, guid VARCHAR NOT NULL)")
    con.execute("INSERT INTO downloads (guid) VALUES ('g')")
    con.commit()
    con.close()
    return db


def _rows(db: Path) -> int:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return int(con.execute("SELECT count(*) FROM downloads").fetchone()[0])
    finally:
        con.close()


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list[Any] = []

    async def new_page(self) -> object:
        page = object()
        self.pages.append(page)
        return page


class _RecordingBrowserType:
    """Captures the download-row count at the moment Chromium would start."""

    def __init__(self, db: Path) -> None:
        self._db = db
        self.rows_at_launch: int | None = None

    async def launch_persistent_context(self, user_data_dir: str, **_: Any) -> _FakeContext:
        self.rows_at_launch = _rows(self._db)
        return _FakeContext()


async def _open(browser_type: Any, kind: str) -> None:
    await launch_helpers._open_browser_context(
        browser_type=browser_type,
        kind=kind,
        profile="dl-persona",
        session_user_data_dir=None,
        headless=False,
        viewport_kwargs={},
        ctx_video_kwargs={},
        ctx_har_kwargs={},
        launch_kwargs={},
    )


async def test_chromium_profile_is_pruned_before_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    db = _seed(personas.engine_profile_dir(persona="dl-persona", kind="chromium"))
    browser_type = _RecordingBrowserType(db)

    await _open(browser_type, "chromium")

    assert browser_type.rows_at_launch == 0


@pytest.mark.parametrize("kind", ["firefox", "webkit"])
async def test_other_engines_are_not_pruned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str) -> None:
    """Only Chromium keeps a ``History`` database; the others are never touched."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    calls: list[Path] = []
    monkeypatch.setattr(launch_helpers, "prune_download_history", calls.append)
    db = _seed(personas.engine_profile_dir(persona="dl-persona", kind=kind))

    await _open(_RecordingBrowserType(db), kind)

    assert calls == []
    assert _rows(db) == 1


async def _open_session(browser_type: Any, kind: str, session_dir: Path) -> None:
    await launch_helpers._open_browser_context(
        browser_type=browser_type,
        kind=kind,
        profile=None,
        session_user_data_dir=str(session_dir),
        headless=False,
        viewport_kwargs={},
        ctx_video_kwargs={},
        ctx_har_kwargs={},
        launch_kwargs={},
    )


async def test_a_session_tmpdir_is_pruned_before_launch(tmp_path: Path) -> None:
    """``session=True`` reuses one tmpdir per label for the daemon's lifetime, so
    its second launch opens a History holding the first launch's rows -- the
    same crash as a profile, and the process-crash relaunch of a named session
    lands exactly there."""
    session_dir = tmp_path / "octowright-session-dl-chromium-x"
    db = _seed(session_dir)
    browser_type = _RecordingBrowserType(db)

    await _open_session(browser_type, "chromium", session_dir)

    assert browser_type.rows_at_launch == 0


# Chromium's SingletonLock is a symlink, and the stale-lock prune, only on
# POSIX; on Windows the download prune runs without it (singleton_locks).
@pytest.mark.skipif(os.name == "nt", reason="SingletonLock symlinks are POSIX-only")
async def test_a_session_tmpdir_left_locked_by_a_crash_is_still_pruned(tmp_path: Path) -> None:
    """A crashed Chromium leaves its SingletonLock behind, and the prune refuses
    a locked dir. The stale-lock prune must run first, as it does for a profile,
    or the relaunch after the crash is the one launch that is not protected."""
    import socket

    session_dir = tmp_path / "octowright-session-dl-chromium-y"
    db = _seed(session_dir)
    # A lock naming a pid on this host that cannot be running.
    os.symlink(f"{socket.gethostname()}-{2**22 + 12345}", session_dir / "SingletonLock")
    browser_type = _RecordingBrowserType(db)

    await _open_session(browser_type, "chromium", session_dir)

    assert browser_type.rows_at_launch == 0
    assert not (session_dir / "SingletonLock").is_symlink()


@pytest.mark.parametrize("kind", ["firefox", "webkit"])
async def test_other_engines_session_dirs_are_not_touched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(launch_helpers, "prune_download_history", calls.append)
    session_dir = tmp_path / f"octowright-session-dl-{kind}-z"
    db = _seed(session_dir)

    await _open_session(_RecordingBrowserType(db), kind, session_dir)

    assert calls == []


async def test_the_prune_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Blocking SQLite (2 s busy wait, DELETE, fsync) must not stall every other
    session's callbacks during a launch; it runs in a worker thread and is still
    finished before the browser starts."""
    import threading

    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    db = _seed(personas.engine_profile_dir(persona="dl-persona", kind="chromium"))
    loop_thread = threading.get_ident()
    threads: list[int] = []
    real = launch_helpers.prune_download_history

    def _spy(user_data_dir: Path) -> None:
        threads.append(threading.get_ident())
        real(user_data_dir)

    monkeypatch.setattr(launch_helpers, "prune_download_history", _spy)
    browser_type = _RecordingBrowserType(db)

    await _open(browser_type, "chromium")

    assert threads and threads[0] != loop_thread
    assert browser_type.rows_at_launch == 0
