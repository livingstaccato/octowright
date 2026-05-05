# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``browser_launch`` defaults to persistent profile when a label is given.

Tests assert behaviour at the ``BrowserPool.launch`` level using a real
headless chromium so we exercise the actual user-data-dir wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Yield a BrowserPool with profiles + recordings dirs redirected to tmp_path."""
    pytest.importorskip("playwright")
    import octowright.browser_pool.pool as _pool
    from octowright import defaults as _defaults
    from octowright import personas as _personas
    from octowright import profiles as _profiles
    from octowright.browser_pool import BrowserPool

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
    # Cleanup happens via pool.shutdown in the test body.


@pytest.mark.asyncio
async def test_label_alone_promotes_to_persistent_profile(isolated_pool) -> None:
    """``label='acct'`` with no explicit profile = becomes profile 'acct'."""
    pool, profiles_dir = isolated_pool
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body></body></html>",
            headed=False,
            label="acct",
            viewport_w=400,
            viewport_h=300,
        )
        assert result["profile"] == "acct", "label should have been promoted to profile"
        # The persistent user-data-dir for chromium under persona 'acct' should exist.
        assert (profiles_dir / "acct" / "chromium").is_dir()
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_ephemeral_flag_blocks_promotion(isolated_pool) -> None:
    """``label='acct', ephemeral=True`` keeps the profile=None / no user-data-dir."""
    pool, profiles_dir = isolated_pool
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body></body></html>",
            headed=False,
            label="acct",
            viewport_w=400,
            viewport_h=300,
            ephemeral=True,
        )
        assert result["profile"] is None, "ephemeral=True must NOT promote label to profile"
        # No persistent dir should exist.
        assert not (profiles_dir / "acct").exists()
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_explicit_profile_overrides_label(isolated_pool) -> None:
    """``label='X', profile='Y'`` keeps profile='Y' (no override either way)."""
    pool, profiles_dir = isolated_pool
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body></body></html>",
            headed=False,
            label="X",
            profile="Y",
            viewport_w=400,
            viewport_h=300,
        )
        assert result["profile"] == "Y"
        assert (profiles_dir / "Y" / "chromium").is_dir()
        assert not (profiles_dir / "X").exists()
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_no_label_no_profile_stays_ephemeral(isolated_pool) -> None:
    """Neither label nor profile = anonymous one-off, no on-disk dir."""
    pool, profiles_dir = isolated_pool
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body></body></html>",
            headed=False,
            label=None,
            viewport_w=400,
            viewport_h=300,
        )
        assert result["profile"] is None
        # No profile dir created.
        assert list(profiles_dir.iterdir()) == []
    finally:
        await pool.shutdown()
