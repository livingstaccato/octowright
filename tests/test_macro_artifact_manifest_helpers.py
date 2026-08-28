# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The pure manifest helpers behind the macro-artifact store.

These four functions decide what an artifact manifest *is*: what a fresh one
contains, which fields survive a re-plan, what a caller reads back, and how a
telemetry label is bounded. The 2026-08-27 mutation run left them with
survivors that share one shape -- every default could be replaced with `None`,
every dict key with a different key, and every boundary shifted by one, with
the whole suite still green. Nothing read a manifest that was *missing* the
optional keys, so the defaults themselves were never observed.

They are pure and need no browser, so they are tested directly here rather
than through a plan/run/verify cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from octowright.artifacts.paths import ArtifactStore
from octowright.macros.artifacts import (
    _cap_macro,
    _compact_manifest,
    _merge_existing_manifest,
    _missing_args,
)

# ---------------------------------------------------------------------------
# _cap_macro -- bounds the `macro` metrics label
# ---------------------------------------------------------------------------


def test_a_name_at_the_limit_is_left_alone() -> None:
    """Exactly 100 characters is *not* over the limit.

    `len(name) > 100` and `>= 100` differ only here, and capping a name that
    was already within bounds silently renames a metric series.
    """
    name = "x" * 100
    assert _cap_macro(name) == name


def test_a_name_past_the_limit_is_truncated_to_the_limit() -> None:
    """101 characters is over, and the result must land exactly on the limit.

    Pinning the output length is what makes the truncation point meaningful:
    `name[:97] + "..."` and `name[:98] + "..."` both "work" and only one keeps
    the label at its documented bound.
    """
    capped = _cap_macro("y" * 101)
    assert len(capped) == 100
    assert capped.endswith("...")
    assert capped.startswith("y" * 97)


def test_capping_returns_a_string() -> None:
    """Concatenation, not arithmetic -- `+` swapped for `-` raises TypeError."""
    assert isinstance(_cap_macro("z" * 500), str)


# ---------------------------------------------------------------------------
# _missing_args -- which declared parameters the caller did not supply
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("parameters", "args", "expected"),
    [
        ({"user": {}, "pass": {}}, {"user": "u"}, ["pass"]),  # dict form
        (["user", "pass"], {"user": "u"}, ["pass"]),  # list form
        (None, {}, []),  # neither -- no declared parameters
        ("nonsense", {}, []),  # not a container at all
        ({"user": {}}, {"user": "u"}, []),  # nothing missing
    ],
)
def test_missing_args_across_parameter_shapes(parameters: Any, args: dict[str, Any], expected: list[str]) -> None:
    """All three branches, including the `else: required = []` fallback.

    A macro may declare parameters as a mapping or a sequence, and older
    macros declare none at all. Only the dict form was exercised, so the list
    branch and the fallback could each be replaced with `None` unnoticed.
    """
    assert _missing_args({"parameters": parameters}, args) == expected


# ---------------------------------------------------------------------------
# _merge_existing_manifest -- what survives a re-plan
# ---------------------------------------------------------------------------


def _write(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_merge_preserves_the_four_carried_fields(tmp_path: Path) -> None:
    """Re-planning must not discard run history or configured critical points."""
    path = _write(
        tmp_path / "artifact.json",
        {
            "created_at": "2026-01-01T00:00:00Z",
            "latest_run": {"run_id": "run_0007"},
            "exports": [{"format": "python"}],
            "critical_points": [{"id": "cp1"}],
        },
    )
    merged = _merge_existing_manifest(path, {"created_at": "NEW", "name": "login"})

    assert merged["created_at"] == "2026-01-01T00:00:00Z"
    assert merged["latest_run"] == {"run_id": "run_0007"}
    assert merged["exports"] == [{"format": "python"}]
    assert merged["critical_points"] == [{"id": "cp1"}]
    assert merged["name"] == "login"  # fields the existing manifest lacks come from the new one


def test_merge_unions_metadata_with_the_new_manifest_winning(tmp_path: Path) -> None:
    """Both sides contribute, and a collision resolves toward the fresh plan."""
    path = _write(tmp_path / "artifact.json", {"metadata": {"keep": 1, "shared": "old"}})
    merged = _merge_existing_manifest(path, {"metadata": {"add": 2, "shared": "new"}})

    assert merged["metadata"] == {"keep": 1, "add": 2, "shared": "new"}


@pytest.mark.parametrize("bad", ["not-a-dict-metadata", 42, None, []])
def test_merge_tolerates_non_dict_metadata_on_either_side(tmp_path: Path, bad: Any) -> None:
    """A hand-edited or corrupted manifest must not break planning."""
    path = _write(tmp_path / "artifact.json", {"metadata": bad})
    assert _merge_existing_manifest(path, {"metadata": {"a": 1}})["metadata"] == {"a": 1}

    path2 = _write(tmp_path / "b" / "artifact.json", {"metadata": {"a": 1}})
    assert _merge_existing_manifest(path2, {"metadata": bad})["metadata"] == {"a": 1}


def test_merge_returns_the_new_manifest_when_there_is_nothing_to_merge(tmp_path: Path) -> None:
    """Three give-up paths: absent file, invalid JSON, and a non-object payload."""
    fresh = {"name": "login", "metadata": {"a": 1}}

    assert _merge_existing_manifest(tmp_path / "missing.json", fresh) == fresh

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _merge_existing_manifest(bad, fresh) == fresh

    listy = _write(tmp_path / "list.json", ["not", "an", "object"])
    assert _merge_existing_manifest(listy, fresh) == fresh


# ---------------------------------------------------------------------------
# _compact_manifest -- what a reader gets back
# ---------------------------------------------------------------------------


def test_compact_supplies_container_defaults_for_a_minimal_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defaults are the point, and nothing had ever read a manifest without them.

    Every caller iterates `exports` / `critical_points` and indexes `metadata`,
    so a `None` default turns a sparse manifest into a TypeError at the call
    site rather than an empty list here.
    """
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "rec"))
    store = ArtifactStore(recordings_dir=tmp_path / "rec")
    path = store.macro_dir("login") / "artifact.json"
    _write(path, {"artifact_type": "macro", "name": "login"})

    compact = _compact_manifest(store, path)

    assert compact is not None
    assert compact["exports"] == []
    assert compact["critical_points"] == []
    assert compact["metadata"] == {}
    assert compact["path"] == str(path.resolve())


def test_compact_reads_back_each_field_it_promises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every key is looked up by name; a wrong key silently yields None."""
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "rec"))
    store = ArtifactStore(recordings_dir=tmp_path / "rec")
    path = store.macro_dir("login") / "artifact.json"
    payload = {
        "artifact_type": "macro",
        "name": "login",
        "source": {"macro": "login"},
        "parameters": {"user": "tanuki"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "latest_run": {"run_id": "run_0001"},
        "exports": [{"format": "python"}],
        "critical_points": [{"id": "cp1"}],
        "metadata": {"note": "hi"},
    }
    _write(path, payload)

    compact = _compact_manifest(store, path)
    assert compact is not None
    for key in ("artifact_type", "name", "source", "created_at", "updated_at", "latest_run"):
        assert compact[key] == payload[key], key
    assert compact["exports"] == payload["exports"]
    assert compact["critical_points"] == payload["critical_points"]
    assert compact["metadata"] == payload["metadata"]
    assert compact["parameters"] == {"user": "tanuki"}


def test_compact_returns_none_for_unreadable_manifests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent, malformed, and non-object payloads are all "no manifest"."""
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "rec"))
    store = ArtifactStore(recordings_dir=tmp_path / "rec")
    base = store.macro_dir("login")

    assert _compact_manifest(store, base / "artifact.json") is None

    bad = base / "artifact.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _compact_manifest(store, bad) is None

    _write(bad, ["not", "an", "object"])
    assert _compact_manifest(store, bad) is None
