# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for octowright.profiles.

Covers ``list_profiles`` (per-engine inventory), ``delete_profile``
(single engine dir), and ``delete_persona`` (whole persona).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright import profiles as _profiles


@pytest.fixture
def tmp_profiles_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect PROFILES_DIR to a fresh tmp dir for each test."""
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    # Both modules read PROFILES_DIR at attribute time, so patch both.
    from octowright import personas as _personas

    monkeypatch.setattr(_profiles, "PROFILES_DIR", pdir)
    monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)
    return pdir


def _make_profile(profiles_dir: Path, persona: str, kind: str, *, files: dict[str, bytes] | None = None) -> Path:
    engine_dir = profiles_dir / persona / kind
    engine_dir.mkdir(parents=True)
    for name, content in (files or {"Cookies": b"\x00" * 64}).items():
        (engine_dir / name).write_bytes(content)
    # Stub profile.yaml so the persona looks real.
    (profiles_dir / persona / "profile.yaml").write_text(f"name: {persona}\n", encoding="utf-8")
    return engine_dir


# ---------------------------------------------------------------------------
# list_profiles
# ---------------------------------------------------------------------------


def test_list_profiles_returns_empty_when_dir_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(_profiles, "PROFILES_DIR", missing)
    assert _profiles.list_profiles() == []


def test_list_profiles_returns_empty_when_dir_empty(tmp_profiles_dir: Path) -> None:
    assert _profiles.list_profiles() == []


def test_list_profiles_reports_each_engine(tmp_profiles_dir: Path) -> None:
    _make_profile(tmp_profiles_dir, "dante", "webkit")
    _make_profile(tmp_profiles_dir, "dante", "firefox")
    _make_profile(tmp_profiles_dir, "ops", "chromium")

    rows = _profiles.list_profiles()
    by_key = {(r["name"], r["kind"]) for r in rows}
    assert by_key == {("dante", "webkit"), ("dante", "firefox"), ("ops", "chromium")}

    for r in rows:
        assert r["size_bytes"] > 0
        assert r["last_used"].endswith("Z")
        assert Path(r["path"]).exists()


def test_list_profiles_filters_by_kind(tmp_profiles_dir: Path) -> None:
    _make_profile(tmp_profiles_dir, "dante", "webkit")
    _make_profile(tmp_profiles_dir, "dante", "firefox")
    rows = _profiles.list_profiles(kind="webkit")
    assert len(rows) == 1
    assert rows[0]["kind"] == "webkit"


def test_list_profiles_skips_files_at_top_level(tmp_profiles_dir: Path) -> None:
    """A stray file in PROFILES_DIR (not a persona dir) must not crash listing."""
    (tmp_profiles_dir / "stray.txt").write_text("ignored")
    _make_profile(tmp_profiles_dir, "dante", "webkit")
    rows = _profiles.list_profiles()
    assert len(rows) == 1
    assert rows[0]["name"] == "dante"


def test_list_profiles_sorted_most_recent_first(tmp_profiles_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    older = _make_profile(tmp_profiles_dir, "dante", "webkit")
    newer = _make_profile(tmp_profiles_dir, "ops", "firefox")
    # Force a known mtime ordering: older 1000, newer 2000.
    import os

    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    rows = _profiles.list_profiles()
    assert [r["name"] for r in rows] == ["ops", "dante"]


# ---------------------------------------------------------------------------
# delete_profile (one engine)
# ---------------------------------------------------------------------------


def test_delete_profile_removes_the_engine_dir(tmp_profiles_dir: Path) -> None:
    engine_dir = _make_profile(tmp_profiles_dir, "dante", "webkit")
    _make_profile(tmp_profiles_dir, "dante", "firefox")  # sibling stays
    assert engine_dir.exists()

    deleted = _profiles.delete_profile("webkit", "dante")
    assert deleted == engine_dir
    assert not engine_dir.exists()
    # Sibling engine and persona.yaml are untouched.
    assert (tmp_profiles_dir / "dante" / "firefox").exists()
    assert (tmp_profiles_dir / "dante" / "profile.yaml").exists()


def test_delete_profile_raises_with_listing_hint(tmp_profiles_dir: Path) -> None:
    with pytest.raises(FileNotFoundError, match="profile_list"):
        _profiles.delete_profile("webkit", "ghost")


# ---------------------------------------------------------------------------
# delete_persona (whole persona tree)
# ---------------------------------------------------------------------------


def test_delete_persona_removes_everything(tmp_profiles_dir: Path) -> None:
    _make_profile(tmp_profiles_dir, "dante", "webkit")
    _make_profile(tmp_profiles_dir, "dante", "firefox")
    persona_root = tmp_profiles_dir / "dante"
    assert persona_root.exists()

    deleted = _profiles.delete_persona("dante")
    assert deleted == persona_root
    assert not persona_root.exists()


def test_delete_persona_raises_with_listing_hint(tmp_profiles_dir: Path) -> None:
    with pytest.raises(FileNotFoundError, match="persona_list"):
        _profiles.delete_persona("ghost")


# ---------------------------------------------------------------------------
# profile_dir delegation
# ---------------------------------------------------------------------------


def test_profile_dir_routes_through_persona_layout(tmp_profiles_dir: Path) -> None:
    p = _profiles.profile_dir("webkit", "dante")
    # Persona-first layout: <PROFILES>/<persona>/<kind>/
    assert p == tmp_profiles_dir / "dante" / "webkit"
