# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the post-upgrade "what's new" notice."""

from __future__ import annotations

from pathlib import Path

from octowright import upgrade

#: A headline, not a paragraph. The longest backfilled title is 61 chars.
MAX_TITLE_CHARS = 80

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
    # every highlight TITLE shows up in the rendered banner. Bodies deliberately
    # do not: they open with the sentence the title condenses, so rendering both
    # stutters, and five paragraphs is not a banner. octowright_status still
    # hands the agent the full entry.
    for entry in notice["highlights"]:
        assert entry["title"] in banner
        assert entry["body"] not in banner


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
        "add src/octowright/upgrade/highlights/<version>.json when bumping VERSION."
    )
    assert upgrade.HIGHLIGHTS[VERSION], f"highlights for {VERSION!r} must be non-empty"


def test_every_highlight_has_a_usable_title_and_body() -> None:
    """Titles feed a blog headline, so an untitled entry is a headless post.

    Checked across ALL versions, not just the current one: the release guard
    above only ever sees the newest entry, so a backfilled version that lost
    its title would go unnoticed until someone rendered the archive. The title
    must also not simply BE the body -- the failure mode when a release is cut
    in a hurry is pasting the paragraph into both fields, which passes a
    non-empty check and defeats the point.
    """
    for version, entries in upgrade.HIGHLIGHTS.items():
        assert entries, f"{version} has no entries"
        for i, entry in enumerate(entries):
            where = f"{version}[{i}]"
            assert set(entry) == {"title", "body"}, f"{where}: unexpected keys {sorted(entry)}"
            title, body = entry["title"].strip(), entry["body"].strip()
            assert title, f"{where}: empty title"
            assert body, f"{where}: empty body"
            assert len(title) <= MAX_TITLE_CHARS, f"{where}: title is {len(title)} chars, a paragraph not a headline"
            assert title != body, f"{where}: title is the whole body"


def test_highlight_files_are_one_per_version() -> None:
    """The loaded dict must match the files on disk, newest first.

    Pins both halves of the layout: a version whose file was never written is
    absent from the dict, and the ordering is by parsed version rather than by
    filename -- a lexical sort puts "0.10.0" before "0.7.0" and would quietly
    break the release guard that reads the first key.
    """
    on_disk = {p.stem for p in upgrade.HIGHLIGHTS_DIR.glob("*.json")}
    assert on_disk == set(upgrade.HIGHLIGHTS)

    ordered = list(upgrade.HIGHLIGHTS)
    by_version = sorted(ordered, key=lambda v: tuple(int(p) for p in v.split(".")), reverse=True)
    assert ordered == by_version


def test_release_highlights_are_newest_and_synchronized() -> None:
    """The newest curated notice must describe the current release."""
    from octowright.version import VERSION

    assert VERSION == "0.22.1"
    assert next(iter(upgrade.HIGHLIGHTS)) == VERSION
