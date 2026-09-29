# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pruning a Chromium profile's download history before opening it.

Chrome for Testing 153.0.8010.12 (Playwright 1.63's ``chromium-1243``) kills its
own BROWSER process with a use-after-free SIGSEGV on the first download of a
headed run when the profile already holds a download-history row and Playwright
is controlling downloads (``accept_downloads=True``, which octowright always
sets). Measured with raw Playwright, no octowright code involved: 7/7 crashes
with one prior row, 0/6 with the ``downloads`` rows deleted and every other
table left alone. See ``browser_pool/download_history.py`` for the full matrix.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from octowright.browser_pool.download_history import (
    PRUNE_DOWNLOAD_HISTORY_ENV,
    prune_download_history,
    prune_download_history_enabled,
)

# The subset of Chromium 153's History schema this touches, plus one table it
# must NOT touch (``urls`` -- browsing history is not ours to delete).
_SCHEMA = (
    "CREATE TABLE urls (id INTEGER PRIMARY KEY, url LONGVARCHAR)",
    "CREATE TABLE downloads (id INTEGER PRIMARY KEY, guid VARCHAR NOT NULL, target_path LONGVARCHAR NOT NULL)",
    "CREATE TABLE downloads_url_chains (id INTEGER NOT NULL, chain_index INTEGER NOT NULL, url LONGVARCHAR NOT NULL)",
    "CREATE TABLE downloads_slices (download_id INTEGER NOT NULL, offset INTEGER NOT NULL, received_bytes INTEGER)",
)


def _history(profile_dir: Path, *, rows: int = 2, name: str = "Default") -> Path:
    leaf = profile_dir / name
    leaf.mkdir(parents=True, exist_ok=True)
    db = leaf / "History"
    con = sqlite3.connect(db)
    for stmt in _SCHEMA:
        con.execute(stmt)
    con.execute("INSERT INTO urls (url) VALUES ('https://example.test/')")
    for i in range(rows):
        con.execute(
            "INSERT INTO downloads (guid, target_path) VALUES (?, ?)",
            (f"g{i}", f"/tmp/playwright-artifacts-x/g{i}"),
        )
        con.execute("INSERT INTO downloads_url_chains VALUES (?, 0, 'blob:https://example.test/x')", (i + 1,))
        con.execute("INSERT INTO downloads_slices VALUES (?, 0, 10)", (i + 1,))
    con.commit()
    con.close()
    return db


def _count(db: Path, table: str) -> int:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # nosec B608 - fixed test table names
    finally:
        con.close()


# ─── the fix itself ──────────────────────────────────────────────────────────


def test_download_rows_are_deleted(tmp_path: Path) -> None:
    db = _history(tmp_path, rows=3)

    prune_download_history(tmp_path)

    assert _count(db, "downloads") == 0
    assert _count(db, "downloads_url_chains") == 0
    assert _count(db, "downloads_slices") == 0


def test_browsing_history_is_left_alone(tmp_path: Path) -> None:
    """Only the download tables are the trigger; ``urls`` is the user's history."""
    db = _history(tmp_path)

    prune_download_history(tmp_path)

    assert _count(db, "urls") == 1


def test_every_profile_directory_is_pruned(tmp_path: Path) -> None:
    """A user-data-dir can hold more than ``Default``."""
    default = _history(tmp_path)
    second = _history(tmp_path, name="Profile 1")

    prune_download_history(tmp_path)

    assert _count(default, "downloads") == 0
    assert _count(second, "downloads") == 0


def test_an_empty_download_history_is_not_rewritten(tmp_path: Path) -> None:
    """Nothing to delete means no write -- the file's mtime is untouched."""
    db = _history(tmp_path, rows=0)
    os.utime(db, (1_000_000, 1_000_000))

    prune_download_history(tmp_path)

    assert db.stat().st_mtime == 1_000_000


# ─── what it must refuse to touch ────────────────────────────────────────────


def test_a_profile_in_use_is_left_alone(tmp_path: Path) -> None:
    """A lock still present after the stale-lock prune means a live owner."""
    if os.name == "nt":
        pytest.skip("Chromium writes no singleton lock on Windows")
    db = _history(tmp_path)
    (tmp_path / "SingletonLock").symlink_to("otherhost-12345")

    prune_download_history(tmp_path)

    assert _count(db, "downloads") == 2


def test_opting_out_leaves_the_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _history(tmp_path)
    monkeypatch.setenv(PRUNE_DOWNLOAD_HISTORY_ENV, "off")

    prune_download_history(tmp_path)

    assert _count(db, "downloads") == 2


@pytest.mark.parametrize("value", ["", "on", "1", "yes", "anything"])
def test_only_an_explicit_falsey_token_opts_out(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PRUNE_DOWNLOAD_HISTORY_ENV, value)
    assert prune_download_history_enabled() is True


@pytest.mark.parametrize("value", ["0", "off", "false", "no", "never", "none", "disabled", " OFF "])
def test_falsey_tokens_opt_out(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PRUNE_DOWNLOAD_HISTORY_ENV, value)
    assert prune_download_history_enabled() is False


# ─── best-effort: never the reason a launch fails ────────────────────────────


def test_a_missing_history_file_is_a_no_op(tmp_path: Path) -> None:
    (tmp_path / "Default").mkdir()
    prune_download_history(tmp_path)  # must not raise


def test_a_missing_user_data_dir_is_a_no_op(tmp_path: Path) -> None:
    prune_download_history(tmp_path / "does-not-exist")  # must not raise


def test_a_corrupt_history_file_does_not_raise(tmp_path: Path) -> None:
    leaf = tmp_path / "Default"
    leaf.mkdir()
    (leaf / "History").write_bytes(b"this is not a sqlite database" * 50)

    prune_download_history(tmp_path)  # must not raise


def test_a_history_without_download_tables_does_not_raise(tmp_path: Path) -> None:
    """A future schema that renames the tables must degrade to a no-op."""
    leaf = tmp_path / "Default"
    leaf.mkdir()
    con = sqlite3.connect(leaf / "History")
    con.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url LONGVARCHAR)")
    con.commit()
    con.close()

    prune_download_history(tmp_path)  # must not raise
