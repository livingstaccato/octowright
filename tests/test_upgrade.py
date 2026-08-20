# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the post-upgrade "what's new" notice."""

from __future__ import annotations

from pathlib import Path

from octowright import upgrade

# ─── compute_upgrade ─────────────────────────────────────────────────────────


def test_same_version_returns_none() -> None:
    """No version change → no notice."""
    assert upgrade.compute_upgrade("0.7.0", "0.7.0") is None


def test_fresh_install_returns_install_notice() -> None:
    """No last-seen version → a first-run 'install' notice."""
    notice = upgrade.compute_upgrade("0.7.0", None)
    assert notice is not None
    assert notice["kind"] == "install"
    assert notice["previous_version"] is None
    assert notice["current_version"] == "0.7.0"


def test_version_change_returns_upgrade_notice() -> None:
    """A different prior version → an 'upgrade' notice carrying the previous version."""
    notice = upgrade.compute_upgrade("0.7.0", "0.6.1")
    assert notice is not None
    assert notice["kind"] == "upgrade"
    assert notice["previous_version"] == "0.6.1"
    assert notice["current_version"] == "0.7.0"


def test_known_version_carries_curated_highlights() -> None:
    """A version present in HIGHLIGHTS surfaces its curated lines."""
    notice = upgrade.compute_upgrade("0.7.0", "0.6.1")
    assert notice is not None
    assert notice["highlights"]  # non-empty for the shipped version
    assert notice["highlights"] == upgrade.HIGHLIGHTS["0.7.0"]


def test_unknown_version_has_empty_highlights() -> None:
    """A version with no curated highlights still produces a notice (empty list)."""
    notice = upgrade.compute_upgrade("99.99.99", "0.7.0")
    assert notice is not None
    assert notice["highlights"] == []


# ─── load / save last-seen version ───────────────────────────────────────────


def test_save_then_load_roundtrips(tmp_path: Path) -> None:
    state = tmp_path / "upgrade.json"
    upgrade.save_last_seen("0.7.0", path=state)
    assert upgrade.load_last_seen(path=state) == "0.7.0"


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    assert upgrade.load_last_seen(path=tmp_path / "nope.json") is None


def test_load_corrupt_file_returns_none(tmp_path: Path) -> None:
    """A malformed state file must not crash startup — treat as unseen."""
    state = tmp_path / "upgrade.json"
    state.write_text("{not json", encoding="utf-8")
    assert upgrade.load_last_seen(path=state) is None


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    state = tmp_path / "nested" / "dir" / "upgrade.json"
    upgrade.save_last_seen("0.7.0", path=state)
    assert state.exists()
    assert upgrade.load_last_seen(path=state) == "0.7.0"


# ─── render_banner ───────────────────────────────────────────────────────────


def test_banner_includes_version_and_highlights() -> None:
    notice = upgrade.compute_upgrade("0.7.0", "0.6.1")
    assert notice is not None
    banner = upgrade.render_banner(notice)
    assert "0.7.0" in banner
    assert "0.6.1" in banner  # shows where you came from
    # every highlight line shows up in the rendered banner
    for line in notice["highlights"]:
        assert line in banner


def test_install_banner_welcomes_without_previous_version() -> None:
    notice = upgrade.compute_upgrade("0.7.0", None)
    assert notice is not None
    banner = upgrade.render_banner(notice)
    assert "0.7.0" in banner
    # a fresh install has no "from" version to render
    assert "None" not in banner


# ─── announce_upgrade_if_changed (orchestration) ─────────────────────────────


def test_announce_records_echoes_and_marks_seen(tmp_path: Path) -> None:
    """On a version change: record the notice, echo a banner, persist the new version."""
    state = tmp_path / "upgrade.json"
    upgrade.save_last_seen("0.6.0", path=state)
    recorded: dict[str, object] = {}
    echoed: list[str] = []

    notice = upgrade.announce_upgrade_if_changed(
        current="0.7.0",
        path=state,
        set_notice=lambda n: recorded.update(n),
        echo=echoed.append,
    )

    assert notice is not None
    assert recorded["current_version"] == "0.7.0"
    assert echoed and "0.7.0" in echoed[0]
    # the new version is now marked seen so the next run is silent
    assert upgrade.load_last_seen(path=state) == "0.7.0"


def test_announce_noop_when_version_unchanged(tmp_path: Path) -> None:
    """Same version → no notice, no echo, no rewrite."""
    state = tmp_path / "upgrade.json"
    upgrade.save_last_seen("0.7.0", path=state)
    echoed: list[str] = []

    notice = upgrade.announce_upgrade_if_changed(
        current="0.7.0",
        path=state,
        set_notice=lambda n: None,
        echo=echoed.append,
    )

    assert notice is None
    assert echoed == []


# ─── release guard: every shipped version must carry curated highlights ──────


def test_current_version_has_curated_highlights() -> None:
    """A VERSION bump must ship a non-empty HIGHLIGHTS entry, or the post-upgrade
    banner renders an empty 'What's new'. This guard fails loudly at release time
    if HIGHLIGHTS wasn't updated alongside the version bump."""
    from octowright.version import VERSION

    assert VERSION in upgrade.HIGHLIGHTS, (
        f"upgrade.HIGHLIGHTS has no entry for the current version {VERSION!r}; "
        "add a curated highlights list when bumping VERSION (src/octowright/upgrade.py)."
    )
    assert upgrade.HIGHLIGHTS[VERSION], f"highlights for {VERSION!r} must be non-empty"


def test_release_highlights_are_newest_and_synchronized() -> None:
    """The newest curated notice must describe the current release."""
    from octowright.version import VERSION

    assert VERSION == "0.16.2"
    assert next(iter(upgrade.HIGHLIGHTS)) == VERSION
