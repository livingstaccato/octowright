# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Reaping orphaned ``octowright-session-*`` profile dirs.

These tmpdirs are created per ``session=True`` launch and normally removed by
``shutdown_pool`` on clean exit. A SIGKILL'd daemon leaks them; a freshly
elected singleton leader reaps the survivors at startup. The reaper is a pure
filesystem helper so it can be tested without a live pool.
"""

from __future__ import annotations

from pathlib import Path

from octowright.browser_pool.session_dirs import (
    SESSION_TMPDIR_PREFIX,
    reap_stale_session_dirs,
)


def _make_session_dir(base: Path, suffix: str) -> Path:
    d = base / f"{SESSION_TMPDIR_PREFIX}{suffix}"
    d.mkdir()
    (d / "state.json").write_text("{}", encoding="utf-8")
    return d


def test_reaps_orphaned_session_dir(tmp_path: Path) -> None:
    """A leftover octowright-session-* dir (with contents) is removed."""
    stale = _make_session_dir(tmp_path, "sessA-chromium-xyz")

    result = reap_stale_session_dirs(tmp_path)

    assert not stale.exists()
    assert str(stale) in result["removed"]
    assert result["errors"] == []
    assert result["dry_run"] is False


def test_leaves_unrelated_dirs_untouched(tmp_path: Path) -> None:
    """Only dirs matching the session prefix are reaped."""
    stale = _make_session_dir(tmp_path, "sessB-firefox-abc")
    keep = tmp_path / "octowright-recordings"
    keep.mkdir()
    other = tmp_path / "unrelated-tmp"
    other.mkdir()

    result = reap_stale_session_dirs(tmp_path)

    assert not stale.exists()
    assert keep.exists()
    assert other.exists()
    assert result["removed"] == [str(stale)]


def test_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    """dry_run=True lists what would be removed but deletes nothing."""
    stale = _make_session_dir(tmp_path, "sessC-webkit-123")

    result = reap_stale_session_dirs(tmp_path, dry_run=True)

    assert stale.exists()
    assert str(stale) in result["removed"]
    assert result["dry_run"] is True


def test_skips_non_directory_with_prefix(tmp_path: Path) -> None:
    """A plain file carrying the prefix is not a session dir — leave it."""
    bogus = tmp_path / f"{SESSION_TMPDIR_PREFIX}not-a-dir"
    bogus.write_text("x", encoding="utf-8")

    result = reap_stale_session_dirs(tmp_path)

    assert bogus.exists()
    assert result["removed"] == []


def test_missing_temp_dir_returns_empty(tmp_path: Path) -> None:
    """A non-existent base dir yields an empty result, not an error."""
    result = reap_stale_session_dirs(tmp_path / "does-not-exist")

    assert result["removed"] == []
    assert result["errors"] == []
