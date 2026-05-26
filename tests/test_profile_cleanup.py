# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``profile_cleanup`` — sweep orphaned profile dirs."""

from __future__ import annotations

import os
import time
from pathlib import Path

from octowright.profile_cleanup import (
    StaleProfile,
    cleanup_stale,
    find_stale_profiles,
)


def _make_profile(root: Path, persona: str, engine: str, age_days: float) -> Path:
    p = root / persona / engine
    p.mkdir(parents=True)
    (p / "Cookies").write_text("fake")
    # Backdate the engine dir mtime so it counts as old.
    if age_days > 0:
        ts = time.time() - (age_days * 86400)
        os.utime(p, (ts, ts))
    return p


def test_find_stale_returns_old_dirs(tmp_path: Path) -> None:
    _make_profile(tmp_path, "old-acct", "chromium", age_days=45)
    _make_profile(tmp_path, "fresh-acct", "chromium", age_days=0)

    stale = find_stale_profiles(tmp_path, days=30.0)
    persona_names = {s.persona for s in stale}
    assert "old-acct" in persona_names
    assert "fresh-acct" not in persona_names


def test_find_stale_skips_in_use_dirs(tmp_path: Path) -> None:
    """Profiles handed in via the ``in_use`` arg are excluded even if old."""
    in_use_path = _make_profile(tmp_path, "active", "chromium", age_days=45)
    _make_profile(tmp_path, "idle", "chromium", age_days=45)

    stale = find_stale_profiles(tmp_path, days=30.0, in_use=[in_use_path])
    personas = {s.persona for s in stale}
    assert "idle" in personas
    assert "active" not in personas


def test_find_stale_per_engine_independence(tmp_path: Path) -> None:
    """Aging out chromium under a persona must not touch its firefox sibling."""
    _make_profile(tmp_path, "p", "chromium", age_days=45)
    _make_profile(tmp_path, "p", "firefox", age_days=0)

    stale = find_stale_profiles(tmp_path, days=30.0)
    engines = {(s.persona, s.engine) for s in stale}
    assert ("p", "chromium") in engines
    assert ("p", "firefox") not in engines


def test_cleanup_stale_dry_run_removes_nothing(tmp_path: Path) -> None:
    p = _make_profile(tmp_path, "old", "chromium", age_days=45)
    stale = find_stale_profiles(tmp_path, days=30.0)
    summary = cleanup_stale(stale, dry_run=True)
    assert summary == {"removed_count": 0, "removed_bytes": 0, "errors": []}
    assert p.exists()


def test_cleanup_stale_apply_removes_dir(tmp_path: Path) -> None:
    p = _make_profile(tmp_path, "old", "chromium", age_days=45)
    stale = find_stale_profiles(tmp_path, days=30.0)
    summary = cleanup_stale(stale, dry_run=False)
    assert summary["removed_count"] == 1
    assert summary["removed_bytes"] > 0
    assert summary["errors"] == []
    assert not p.exists()


def test_cleanup_stale_surfaces_rmtree_oserror(tmp_path: Path, monkeypatch) -> None:
    """A failed rmtree must land in ``errors`` rather than being silently swallowed."""
    import octowright.profile_cleanup as _pc

    p = _make_profile(tmp_path, "broken", "chromium", age_days=45)
    stale = find_stale_profiles(tmp_path, days=30.0)

    def _boom(path, **kwargs):
        raise OSError("simulated permission denied")

    monkeypatch.setattr(_pc.shutil, "rmtree", _boom)
    summary = cleanup_stale(stale, dry_run=False)
    assert summary["removed_count"] == 0
    assert len(summary["errors"]) == 1
    assert summary["errors"][0]["path"] == str(p)
    assert "simulated permission denied" in summary["errors"][0]["error"]


def test_cleanup_removes_orphaned_persona_dir_when_empty(tmp_path: Path) -> None:
    """Persona dir gets cleaned up too if no engine subdirs remain after sweep."""
    persona = tmp_path / "lonely"
    _make_profile(tmp_path, "lonely", "chromium", age_days=45)
    cleanup_stale(find_stale_profiles(tmp_path, days=30.0), dry_run=False)
    # Persona dir was emptied — should be gone too.
    assert not persona.exists()


def test_find_stale_returns_StaleProfile_with_size_and_age(tmp_path: Path) -> None:
    _make_profile(tmp_path, "p", "chromium", age_days=45)
    stale = find_stale_profiles(tmp_path, days=30.0)
    assert len(stale) == 1
    sp = stale[0]
    assert isinstance(sp, StaleProfile)
    assert sp.size_bytes > 0
    assert sp.age_days >= 30
