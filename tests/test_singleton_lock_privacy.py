# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The lockfile holds the bridge capability token, so it is 0600 for its whole life.

``write_lock`` wrote the token into ``octowright.lock.<pid>.tmp`` under the
process umask (usually 0644) and chmodded it only afterwards, so another
local user could read the token in between -- and the predictable name let
them pre-plant a symlink there. The parent-directory chmod that would have
covered the gap failed silently whenever ``OCTOWRIGHT_LOCK_PATH`` pointed into
a directory the user does not own.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright import singleton

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits and symlinks")


def _info() -> singleton.LeaderInfo:
    return singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=6286,
        mcp_url="http://127.0.0.1:6286/mcp/",
        started_at=0.0,
        token="lock-token",  # pragma: allowlist secret (synthetic fixture)
    )


def test_the_temp_file_is_private_before_the_token_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Observe the temp file at the moment it is renamed, with chmod disabled.

    A file that is only private because of an after-the-fact chmod fails this;
    one created 0600 does not.
    """
    lock = tmp_path / "octowright.lock"
    seen: list[int] = []
    real_replace = os.replace

    def spy_replace(src: Any, dst: Any) -> None:
        seen.append(os.stat(src).st_mode & 0o777)
        real_replace(src, dst)

    monkeypatch.setattr(os, "chmod", lambda *_a, **_k: None)
    monkeypatch.setattr(os, "replace", spy_replace)
    monkeypatch.setattr(Path, "replace", lambda self, target: spy_replace(self, target))
    old_umask = os.umask(0o022)
    try:
        singleton.write_lock(_info(), path=lock)
    finally:
        os.umask(old_umask)
    assert seen == [0o600]
    assert singleton.read_lock(lock) == _info()


def test_a_symlink_planted_at_the_old_temp_name_is_not_followed(tmp_path: Path) -> None:
    lock = tmp_path / "octowright.lock"
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched", encoding="utf-8")
    (tmp_path / f"octowright.lock.{os.getpid()}.tmp").symlink_to(victim)

    singleton.write_lock(_info(), path=lock)

    assert victim.read_text(encoding="utf-8") == "untouched"
    assert singleton.read_lock(lock) == _info()


def test_a_parent_chmod_failure_is_logged_not_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = tmp_path / "shared" / "octowright.lock"
    real_chmod = Path.chmod

    def refuse_parent(self: Path, mode: int, **kwargs: Any) -> None:
        if self == lock.parent:
            raise PermissionError("not the owner")
        real_chmod(self, mode, **kwargs)

    fake_log = MagicMock()
    monkeypatch.setattr(Path, "chmod", refuse_parent)
    monkeypatch.setattr(singleton, "log", fake_log, raising=False)

    singleton.write_lock(_info(), path=lock)

    fake_log.warning.assert_called_once()
    assert fake_log.warning.call_args.args[0] == "singleton.lock_parent_chmod_failed"
    assert (lock.stat().st_mode & 0o777) == 0o600


def test_a_failed_write_leaves_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = tmp_path / "octowright.lock"

    def boom(_src: Any, _dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    monkeypatch.setattr(Path, "replace", lambda self, target: boom(self, target))
    with pytest.raises(OSError, match="disk full"):
        singleton.write_lock(_info(), path=lock)
    assert list(tmp_path.iterdir()) == []
