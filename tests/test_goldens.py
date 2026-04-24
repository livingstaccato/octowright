# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_goldens(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Reload goldens with a patched OCTOWRIGHT_GOLDENS_DIR pointing at tmp_path."""
    monkeypatch.setenv("OCTOWRIGHT_GOLDENS_DIR", str(tmp_path / "goldens"))
    import octowright.goldens as _g

    importlib.reload(_g)
    return _g


SAMPLE_TREE: dict[str, Any] = {
    "role": "RootWebArea",
    "name": "Example Domain",
    "children": [
        {"role": "heading", "name": "Example Domain", "level": 1},
        {"role": "paragraph", "name": "This domain is for use in illustrative examples."},
    ],
}


# ---------------------------------------------------------------------------
# save_golden / load_golden
# ---------------------------------------------------------------------------


def test_save_golden_writes_expected_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    path = g.save_golden(name="My Page", tree=SAMPLE_TREE, url="https://example.com", description="smoke test")
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["name"] == "My Page"
    assert data["description"] == "smoke test"
    assert data["url"] == "https://example.com"
    assert data["tree"] == SAMPLE_TREE
    assert "created_at" in data
    assert "updated_at" in data


def test_save_golden_preserves_created_at_on_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    g.save_golden(name="stable", tree=SAMPLE_TREE)
    first = json.loads((tmp_path / "goldens" / "stable.json").read_text())
    created_at = first["created_at"]

    # Overwrite with a different tree.
    modified = dict(SAMPLE_TREE, name="Updated")
    g.save_golden(name="stable", tree=modified)
    second = json.loads((tmp_path / "goldens" / "stable.json").read_text())
    assert second["created_at"] == created_at, "created_at must not change on overwrite"
    assert second["tree"]["name"] == "Updated"


def test_load_golden_returns_full_dict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    g.save_golden(name="load-me", tree=SAMPLE_TREE, url="https://example.com")
    data = g.load_golden("load-me")
    assert data["name"] == "load-me"
    assert data["tree"] == SAMPLE_TREE


def test_load_golden_raises_file_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError):
        g.load_golden("does-not-exist")


# ---------------------------------------------------------------------------
# list_goldens
# ---------------------------------------------------------------------------


def test_list_goldens_returns_sorted_desc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    g.save_golden(name="alpha", tree=SAMPLE_TREE)
    g.save_golden(name="beta", tree=SAMPLE_TREE)
    g.save_golden(name="gamma", tree=SAMPLE_TREE)
    items = g.list_goldens()
    assert len(items) == 3
    # updated_at should be descending (gamma was last saved)
    timestamps = [i["updated_at"] for i in items]
    assert timestamps == sorted(timestamps, reverse=True)


def test_list_goldens_empty_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    assert g.list_goldens() == []


# ---------------------------------------------------------------------------
# delete_golden
# ---------------------------------------------------------------------------


def test_delete_golden_removes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    g.save_golden(name="to-delete", tree=SAMPLE_TREE)
    path = g.delete_golden("to-delete")
    assert not path.exists()


def test_delete_golden_raises_if_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError):
        g.delete_golden("ghost")


# ---------------------------------------------------------------------------
# diff_trees
# ---------------------------------------------------------------------------


def test_diff_trees_identical_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    diffs = g.diff_trees(SAMPLE_TREE, SAMPLE_TREE)
    assert diffs == []


def test_diff_trees_leaf_value_changed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    expected = {"role": "heading", "name": "Hello"}
    actual = {"role": "heading", "name": "World"}
    diffs = g.diff_trees(expected, actual)
    assert len(diffs) == 1
    assert diffs[0]["op"] == "changed"
    assert diffs[0]["expected"] == "Hello"
    assert diffs[0]["actual"] == "World"
    assert "name" in diffs[0]["path"]


def test_diff_trees_key_added_in_actual(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    expected: dict[str, Any] = {"role": "button"}
    actual: dict[str, Any] = {"role": "button", "expanded": True}
    diffs = g.diff_trees(expected, actual)
    ops = {d["op"] for d in diffs}
    assert "added" in ops
    added = next(d for d in diffs if d["op"] == "added")
    assert added["actual"] is True
    assert added["expected"] is None


def test_diff_trees_key_removed_from_actual(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    expected: dict[str, Any] = {"role": "button", "disabled": True}
    actual: dict[str, Any] = {"role": "button"}
    diffs = g.diff_trees(expected, actual)
    ops = {d["op"] for d in diffs}
    assert "removed" in ops
    removed = next(d for d in diffs if d["op"] == "removed")
    assert removed["expected"] is True
    assert removed["actual"] is None


def test_diff_trees_array_length_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    expected = {"children": [{"role": "a"}, {"role": "b"}]}
    actual = {"children": [{"role": "a"}]}
    diffs = g.diff_trees(expected, actual)
    # The second element is missing in actual → "removed"
    removed = [d for d in diffs if d["op"] == "removed"]
    assert len(removed) >= 1
    assert "children/1" in removed[0]["path"]
