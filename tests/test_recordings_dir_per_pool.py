# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-pool recordings-root routing (write side).

``BrowserPool(recordings_dir=...)`` lets an embedder that runs concurrent
pools in one process route each pool's launch artefacts (JSONL log, video,
HAR, downloads) to a distinct root, instead of the single process-global
``defaults.RECORDINGS_DIR``. These tests pin the write-side plumbing:

  * the pool exposes and stores the configured root;
  * the video + HAR builders honour an explicit ``recordings_dir`` while
    still defaulting to the (monkeypatchable) module global.

The dashboard/discovery/cleanup readers deliberately stay bound to the global
root — see ``BrowserPool.__init__``'s docstring for that documented gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.browser_pool import launch_helpers
from octowright.browser_pool.pool import BrowserPool


def test_pool_defaults_recordings_dir_to_global(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No arg → the pool uses the process-global RECORDINGS_DIR."""
    import octowright.browser_pool.pool as pool_mod

    monkeypatch.setattr(pool_mod, "RECORDINGS_DIR", tmp_path)
    assert BrowserPool().recordings_dir == tmp_path


def test_pool_stores_explicit_recordings_dir(tmp_path: Path) -> None:
    """An explicit recordings_dir is stored and surfaced via the property."""
    root = tmp_path / "pool-a"
    assert BrowserPool(recordings_dir=root).recordings_dir == root


def test_pool_expands_user_in_recordings_dir() -> None:
    """A ``~``-prefixed root is expanded so it doesn't create a literal ~ dir."""
    pool = BrowserPool(recordings_dir=Path("~/octowright-test-root"))
    assert "~" not in str(pool.recordings_dir)
    assert pool.recordings_dir.is_absolute()


def test_build_video_kwargs_routes_to_explicit_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An explicit recordings_dir wins over the module global for the video dir."""
    monkeypatch.setattr(launch_helpers, "RECORDINGS_DIR", tmp_path / "global")
    pool_root = tmp_path / "pool-b"
    log_path = pool_root / "20260101T000000Z-chromium-abc123-mylabel.jsonl"
    _kwargs, video_dir = launch_helpers._build_video_kwargs(
        True, True, False, 1280, 800, log_path=log_path, recordings_dir=pool_root
    )
    assert video_dir is not None
    assert video_dir.is_relative_to(pool_root / "videos")


def test_build_video_kwargs_defaults_to_global(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No recordings_dir → resolves the module global at call time (monkeypatchable)."""
    monkeypatch.setattr(launch_helpers, "RECORDINGS_DIR", tmp_path)
    log_path = tmp_path / "20260101T000000Z-chromium-abc123-mylabel.jsonl"
    _kwargs, video_dir = launch_helpers._build_video_kwargs(True, True, False, 1280, 800, log_path=log_path)
    assert video_dir is not None
    assert video_dir.is_relative_to(tmp_path / "videos")


def test_build_video_kwargs_dir_name_carries_the_launch_identity(tmp_path: Path) -> None:
    """The video dir is findable by label/instance_id like every other artifact
    type, instead of a random id only recoverable from the launch/close result."""
    log_path = tmp_path / "20260101T000000Z-chromium-abc123-mylabel.jsonl"
    _kwargs, video_dir = launch_helpers._build_video_kwargs(
        True, True, False, 1280, 800, log_path=log_path, recordings_dir=tmp_path
    )
    assert video_dir is not None
    assert video_dir.name == log_path.stem


def test_build_har_kwargs_routes_to_explicit_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A relative HAR path lands under the explicit recordings_dir, not the global."""
    monkeypatch.setattr(launch_helpers, "RECORDINGS_DIR", tmp_path / "global")
    pool_root = tmp_path / "pool-c"
    log_path = pool_root / "session.jsonl"
    har_path, _kwargs = launch_helpers._build_har_kwargs(
        har=True,
        har_path_opt="capture.har",
        har_mode="minimal",
        har_url_filter=None,
        har_content=None,
        log_path=log_path,
        recordings_dir=pool_root,
    )
    assert har_path is not None
    assert har_path.is_relative_to(pool_root)


def test_build_har_kwargs_rejects_absolute_outside_explicit_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Containment follows the explicit root: an absolute HAR under the module
    global but OUTSIDE the pool's root is rejected."""
    global_root = tmp_path / "global"
    global_root.mkdir()
    monkeypatch.setattr(launch_helpers, "RECORDINGS_DIR", global_root)
    pool_root = tmp_path / "pool-d"
    outside = global_root / "evil.har"  # under the global, not under pool_root
    with pytest.raises(ValueError, match="resolves outside"):
        launch_helpers._build_har_kwargs(
            har=True,
            har_path_opt=str(outside),
            har_mode="minimal",
            har_url_filter=None,
            har_content=None,
            log_path=pool_root / "session.jsonl",
            recordings_dir=pool_root,
        )
