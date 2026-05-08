# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

from octowright.http.artifacts import _build_component, _find_screenshot_entries


def test_build_component_uses_one_stat_for_existing_file(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """One stat() should cover both existence and size — no separate exists() probe."""
    target = tmp_path / "thing.bin"
    target.write_bytes(b"hello world")

    stat_calls = 0
    real_stat = Path.stat

    def counting_stat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal stat_calls
        stat_calls += 1
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counting_stat)

    out = _build_component(target)
    assert out["exists"] is True
    assert out["size_bytes"] == 11
    assert out["path"] == str(target)
    assert stat_calls == 1


def test_build_component_returns_zero_for_missing_path(tmp_path: Path) -> None:
    out = _build_component(tmp_path / "nope.bin")
    assert out == {"size_bytes": 0, "size_human": "0 B", "path": str(tmp_path / "nope.bin"), "exists": False}


def test_build_component_handles_none_path() -> None:
    out = _build_component(None)
    assert out["path"] is None
    assert out["exists"] is False
    assert out["size_bytes"] == 0


def test_find_screenshot_entries_matches_only_token_boundary(tmp_path: Path) -> None:
    """The strict matcher should accept the two known producer patterns and
    reject filenames that merely contain the id as a substring."""
    sid = "abc123def456"  # pragma: allowlist secret
    # Producer 1: "{instance_id}-fail-{ts}.png"
    fail_shot = tmp_path / f"{sid}-fail-20260101T000000Z.png"
    fail_shot.write_bytes(b"png1")
    # Producer 2: "{log_path.stem}.png" where stem ends with "-{instance_id}"
    explicit = tmp_path / f"20260101T000000Z-chromium-{sid}.png"
    explicit.write_bytes(b"png2")
    # Decoy: contains the id as a substring inside an unrelated token —
    # e.g. someone named a file with the id concatenated. Old loose matcher
    # would have included this; the new one must reject it.
    decoy = tmp_path / f"unrelated{sid}xyz.png"
    decoy.write_bytes(b"png3")

    entries, total = _find_screenshot_entries(tmp_path, sid)
    assert sorted(entries) == sorted([str(fail_shot), str(explicit)])
    assert total == 8  # 4 + 4
    assert str(decoy) not in entries


def test_find_screenshot_entries_returns_empty_for_no_session_id(tmp_path: Path) -> None:
    (tmp_path / "anything.png").write_bytes(b"x")
    assert _find_screenshot_entries(tmp_path, None) == ([], 0)


def test_find_screenshot_entries_caches_dir_listing(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The PNG dir listing is cached by dir mtime so repeated per-session lookups
    against the same dir don't re-scan."""
    from octowright.http import artifacts as art_mod

    art_mod.invalidate_screenshot_dir_cache()
    sid = "abc123def456"  # pragma: allowlist secret
    (tmp_path / f"{sid}-fail-x.png").write_bytes(b"a")
    (tmp_path / f"20260101-chromium-{sid}.png").write_bytes(b"b")

    glob_count = 0
    real_glob = Path.glob

    def counting_glob(self, pattern):  # type: ignore[no-untyped-def]
        nonlocal glob_count
        if pattern == "*.png":
            glob_count += 1
        yield from real_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", counting_glob)

    # First call: glob runs once and result is cached.
    e1, s1 = _find_screenshot_entries(tmp_path, sid)
    assert glob_count == 1
    # Second call against same dir + same session: cache hit, no re-scan.
    e2, s2 = _find_screenshot_entries(tmp_path, sid)
    assert glob_count == 1
    assert e1 == e2 and s1 == s2
    # Third call with different session_id (but same dir) also hits cache.
    _find_screenshot_entries(tmp_path, "unrelatedsid")
    assert glob_count == 1


def test_find_screenshot_entries_invalidates_on_dir_mtime_change(tmp_path: Path) -> None:
    """Adding a new PNG bumps dir mtime → cache invalidates → new file picked up."""
    from octowright.http import artifacts as art_mod

    art_mod.invalidate_screenshot_dir_cache()
    sid = "rotateidwxyz"  # pragma: allowlist secret
    (tmp_path / f"{sid}-fail-1.png").write_bytes(b"a")

    e1, _ = _find_screenshot_entries(tmp_path, sid)
    assert len(e1) == 1

    # Add a second screenshot for the same session.
    import os

    (tmp_path / f"{sid}-fail-2.png").write_bytes(b"b")
    new_mtime = tmp_path.stat().st_mtime_ns + 1_000_000_000
    os.utime(tmp_path, ns=(new_mtime, new_mtime))

    e2, _ = _find_screenshot_entries(tmp_path, sid)
    assert len(e2) == 2
