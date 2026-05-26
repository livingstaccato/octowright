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
    """Reload goldens with a patched OCTOWRIGHT_GOLDENS_DIR pointing at tmp_path.

    GOLDENS_DIR is owned by defaults; reload it first so goldens picks up
    the fresh value.
    """
    monkeypatch.setenv("OCTOWRIGHT_GOLDENS_DIR", str(tmp_path / "goldens"))
    from octowright import defaults

    importlib.reload(defaults)
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
    path = g.save_golden(name="My Page", tree=SAMPLE_TREE, url="https://octowright.com", description="smoke test")
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["name"] == "My Page"
    assert data["description"] == "smoke test"
    assert data["url"] == "https://octowright.com"
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
    g.save_golden(name="load-me", tree=SAMPLE_TREE, url="https://octowright.com")
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


def test_diff_trees_inserted_head_does_not_cascade(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Inserting a notification banner at index 0 must produce exactly one
    ``added`` diff, not an O(n) cascade of ``changed`` diffs as positional
    matching would generate."""
    g = _import_goldens(monkeypatch, tmp_path)
    expected = {
        "role": "RootWebArea",
        "name": "App",
        "children": [
            {"role": "heading", "name": "First"},
            {"role": "paragraph", "name": "Second"},
            {"role": "paragraph", "name": "Third"},
        ],
    }
    actual = {
        "role": "RootWebArea",
        "name": "App",
        "children": [
            {"role": "alert", "name": "Saved!"},
            {"role": "heading", "name": "First"},
            {"role": "paragraph", "name": "Second"},
            {"role": "paragraph", "name": "Third"},
        ],
    }
    diffs = g.diff_trees(expected, actual)
    assert len(diffs) == 1, f"expected single added diff, got {diffs!r}"
    assert diffs[0]["op"] == "added"
    assert diffs[0]["actual"] == {"role": "alert", "name": "Saved!"}


def test_diff_trees_sibling_reorder_no_diff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Swapping two siblings with identical (role, name) should produce no
    diff — identity-keyed matching treats them as semantically equivalent.
    Order is not a semantic property of an accessibility tree under this
    diff: a re-ordered toolbar that exposes the same affordances is the same
    UI to a screen reader, and we want goldens to ratchet on *content*, not
    layout sequence."""
    g = _import_goldens(monkeypatch, tmp_path)
    expected = {
        "children": [
            {"role": "button", "name": "Save"},
            {"role": "button", "name": "Cancel"},
        ],
    }
    actual = {
        "children": [
            {"role": "button", "name": "Cancel"},
            {"role": "button", "name": "Save"},
        ],
    }
    assert g.diff_trees(expected, actual) == []


def test_diff_trees_removed_middle_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Removing a middle child produces a single ``removed`` diff."""
    g = _import_goldens(monkeypatch, tmp_path)
    expected = {
        "children": [
            {"role": "link", "name": "Home"},
            {"role": "link", "name": "About"},
            {"role": "link", "name": "Contact"},
        ],
    }
    actual = {
        "children": [
            {"role": "link", "name": "Home"},
            {"role": "link", "name": "Contact"},
        ],
    }
    diffs = g.diff_trees(expected, actual)
    assert len(diffs) == 1
    assert diffs[0]["op"] == "removed"
    assert diffs[0]["expected"] == {"role": "link", "name": "About"}


def test_diff_trees_changed_within_matched_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A field change on a node matched by (role, name) surfaces as
    ``changed``, not as a remove+add pair."""
    g = _import_goldens(monkeypatch, tmp_path)
    expected = {
        "children": [
            {"role": "button", "name": "Save", "disabled": False},
        ],
    }
    actual = {
        "children": [
            {"role": "button", "name": "Save", "disabled": True},
        ],
    }
    diffs = g.diff_trees(expected, actual)
    assert len(diffs) == 1
    assert diffs[0]["op"] == "changed"
    assert diffs[0]["expected"] is False
    assert diffs[0]["actual"] is True


def test_diff_trees_unkeyed_list_falls_back_to_positional(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A list of plain strings carries no identity signal — fall back to
    positional matching so we still surface diffs for fully unkeyed lists."""
    g = _import_goldens(monkeypatch, tmp_path)
    expected = {"tags": ["alpha", "beta", "gamma"]}
    actual = {"tags": ["alpha", "BETA", "gamma"]}
    diffs = g.diff_trees(expected, actual)
    assert len(diffs) == 1
    assert diffs[0]["op"] == "changed"
    assert diffs[0]["expected"] == "beta"
    assert diffs[0]["actual"] == "BETA"


def test_diff_trees_role_only_uses_label_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Nodes with role but no name use the label/text fallback so siblings
    of the same role are still distinguishable."""
    g = _import_goldens(monkeypatch, tmp_path)
    expected = {
        "children": [
            {"role": "paragraph", "text": "Intro"},
            {"role": "paragraph", "text": "Body"},
        ],
    }
    actual = {
        "children": [
            {"role": "paragraph", "text": "Body"},
            {"role": "paragraph", "text": "Intro"},
        ],
    }
    assert g.diff_trees(expected, actual) == []


# ---------------------------------------------------------------------------
# Path containment — defense in depth around _slug()
# ---------------------------------------------------------------------------


def test_save_golden_rejects_path_traversal_slug(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If ``_slug`` ever returns a traversal-shaped value, ``reject_unsafe_path``
    is the boundary that stops the write from escaping GOLDENS_DIR."""
    g = _import_goldens(monkeypatch, tmp_path)
    # Force _slug to return a traversal segment; the containment guard must
    # raise rather than letting the write escape GOLDENS_DIR.
    monkeypatch.setattr(g, "_slug", lambda name: "../etc/passwd")
    with pytest.raises(ValueError, match="resolves outside"):
        g.save_golden(name="anything", tree=SAMPLE_TREE)


def test_load_golden_rejects_path_traversal_slug(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    monkeypatch.setattr(g, "_slug", lambda name: "../etc/passwd")
    with pytest.raises(ValueError, match="resolves outside"):
        g.load_golden("anything")


def test_delete_golden_rejects_path_traversal_slug(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = _import_goldens(monkeypatch, tmp_path)
    monkeypatch.setattr(g, "_slug", lambda name: "../etc/passwd")
    with pytest.raises(ValueError, match="resolves outside"):
        g.delete_golden("anything")
