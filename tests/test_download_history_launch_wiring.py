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
