# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``session=True`` — third persistence mode (tmpdir, daemon-lifetime)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("playwright")
    from octowright import defaults as _defaults
    from octowright import personas as _personas
    from octowright import pool as _pool
    from octowright import profiles as _profiles
    from octowright.pool import BrowserPool

    rec = tmp_path / "rec"
    profiles = tmp_path / "profiles"
    rec.mkdir()
    profiles.mkdir()
    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_defaults, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_personas, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", profiles)

    pool = BrowserPool()
    yield pool, profiles


@pytest.mark.asyncio
async def test_session_true_creates_tmpdir_not_persistent_dir(isolated_pool) -> None:
    """session=True must NOT create anything under PROFILES_DIR."""
    pool, profiles_root = isolated_pool
    try:
        await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body></body></html>",
            headed=False,
            label="sessA",
            viewport_w=400,
            viewport_h=300,
            session=True,
        )
        # Nothing under PROFILES_DIR for sessA.
        assert not (profiles_root / "sessA").exists()
        # But the tmpdir tracker has an entry.
        assert ("sessA", "chromium") in pool._session_profile_dirs
        tmp = pool._session_profile_dirs[("sessA", "chromium")]
        assert tmp.exists()
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_session_true_reuses_tmpdir_across_launches(isolated_pool) -> None:
    """Same (label, kind) gets the same tmpdir — close+reopen keeps state in-daemon."""
    pool, _ = isolated_pool
    try:
        await pool.launch(
            kind="chromium",
            url="data:text/html,<html></html>",
            headed=False,
            label="sessB",
            viewport_w=400,
            viewport_h=300,
            session=True,
        )
        first_tmp = pool._session_profile_dirs[("sessB", "chromium")]
        await pool.close_all()

        await pool.launch(
            kind="chromium",
            url="data:text/html,<html></html>",
            headed=False,
            label="sessB",
            viewport_w=400,
            viewport_h=300,
            session=True,
        )
        second_tmp = pool._session_profile_dirs[("sessB", "chromium")]
        assert first_tmp == second_tmp, "session tmpdir must be reused across launches"
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_session_and_ephemeral_are_mutually_exclusive(isolated_pool) -> None:
    pool, _ = isolated_pool
    try:
        with pytest.raises(ValueError, match="mutually exclusive"):
            await pool.launch(
                kind="chromium",
                url="data:text/html,<html></html>",
                headed=False,
                label="bad",
                viewport_w=400,
                viewport_h=300,
                session=True,
                ephemeral=True,
            )
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_session_tmpdir_wiped_on_pool_shutdown(isolated_pool) -> None:
    """Daemon shutdown removes session tmpdirs."""
    pool, _ = isolated_pool
    await pool.launch(
        kind="chromium",
        url="data:text/html,<html></html>",
        headed=False,
        label="sessC",
        viewport_w=400,
        viewport_h=300,
        session=True,
    )
    tmp = pool._session_profile_dirs[("sessC", "chromium")]
    assert tmp.exists()
    await pool.shutdown()
    assert not tmp.exists()
    assert pool._session_profile_dirs == {}
