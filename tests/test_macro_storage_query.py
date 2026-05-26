# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-precise tests for the read/lookup/destroy slice of macros.storage.

Companion to test_macro_storage.py: covers list_macros, load_macro, and
delete_macro — including the exact FileNotFoundError messages the MCP layer
relies on when surfacing errors to the LLM.
"""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path
from typing import Any

import pytest


def _import_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    # MACROS_DIR is owned by defaults; reload it first.
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros.storage as _storage

    importlib.reload(_storage)
    return _storage


def _write_recording(tmp_path: Path, lines: list[dict[str, Any]] | None = None) -> Path:
    p = tmp_path / "recording.jsonl"
    rows = lines or [
        {"ts": "2026-04-24T10:00:00Z", "action": "navigate", "url": "https://octowright.com"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# list_macros
# ---------------------------------------------------------------------------


def test_list_macros_returns_empty_when_dir_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """list_macros() returns [] if MACROS_DIR doesn't exist (don't try to glob)."""
    s = _import_storage(monkeypatch, tmp_path)
    assert not s.MACROS_DIR.exists()
    assert s.list_macros() == []


def test_list_macros_returns_empty_for_empty_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """list_macros() returns [] when MACROS_DIR exists but contains no .json files."""
    s = _import_storage(monkeypatch, tmp_path)
    s.MACROS_DIR.mkdir(parents=True, exist_ok=True)
    assert s.list_macros() == []


def test_list_macros_skips_malformed_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """list_macros() silently skips files that fail json.loads (no exception bubbles)."""
    s = _import_storage(monkeypatch, tmp_path)
    s.MACROS_DIR.mkdir(parents=True, exist_ok=True)
    (s.MACROS_DIR / "broken.json").write_text("{not valid", encoding="utf-8")
    rec = _write_recording(tmp_path)
    s.save_macro(recording_path=rec, name="good")

    result = s.list_macros()
    names = [entry["name"] for entry in result]
    assert "good" in names
    assert "broken" not in names


def test_list_macros_only_globs_json_extension(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """list_macros() ignores files with non-.json extensions in MACROS_DIR."""
    s = _import_storage(monkeypatch, tmp_path)
    s.MACROS_DIR.mkdir(parents=True, exist_ok=True)
    (s.MACROS_DIR / "ignore.txt").write_text("not a macro", encoding="utf-8")
    (s.MACROS_DIR / "ignore.yaml").write_text("---\n", encoding="utf-8")
    rec = _write_recording(tmp_path)
    s.save_macro(recording_path=rec, name="real")

    result = s.list_macros()
    names = [entry["name"] for entry in result]
    assert names == ["real"]


def test_list_macros_sorted_by_updated_at_desc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """list_macros() sorts entries by updated_at descending — newest first."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    s.save_macro(recording_path=rec, name="oldest")
    time.sleep(0.01)
    s.save_macro(recording_path=rec, name="middle")
    time.sleep(0.01)
    s.save_macro(recording_path=rec, name="newest")

    result = s.list_macros()
    names = [entry["name"] for entry in result]
    assert names == ["newest", "middle", "oldest"]


def test_list_macros_handles_missing_updated_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """list_macros() puts entries with no updated_at at the end (sort key '')."""
    s = _import_storage(monkeypatch, tmp_path)
    s.MACROS_DIR.mkdir(parents=True, exist_ok=True)

    (s.MACROS_DIR / "with-ts.json").write_text(
        json.dumps(
            {
                "name": "with-ts",
                "updated_at": "2030-01-01T00:00:00Z",
                "actions": [],
            }
        ),
        encoding="utf-8",
    )
    (s.MACROS_DIR / "no-ts.json").write_text(
        json.dumps({"name": "no-ts", "actions": []}),
        encoding="utf-8",
    )

    result = s.list_macros()
    names = [entry["name"] for entry in result]
    assert names == ["with-ts", "no-ts"]


def test_list_macros_entry_uses_data_name_not_stem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When the JSON contains a 'name' field, that is reported (not the file stem)."""
    s = _import_storage(monkeypatch, tmp_path)
    s.MACROS_DIR.mkdir(parents=True, exist_ok=True)
    (s.MACROS_DIR / "filename.json").write_text(
        json.dumps({"name": "Different Name", "actions": []}),
        encoding="utf-8",
    )
    [entry] = s.list_macros()
    assert entry["name"] == "Different Name"


def test_list_macros_falls_back_to_stem_when_name_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If 'name' is absent in the JSON, list_macros uses the file stem."""
    s = _import_storage(monkeypatch, tmp_path)
    s.MACROS_DIR.mkdir(parents=True, exist_ok=True)
    (s.MACROS_DIR / "the-stem.json").write_text(json.dumps({"actions": []}), encoding="utf-8")
    [entry] = s.list_macros()
    assert entry["name"] == "the-stem"


def test_list_macros_action_count_is_actions_length(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """action_count equals len(data['actions']), even for empty/missing list."""
    s = _import_storage(monkeypatch, tmp_path)
    s.MACROS_DIR.mkdir(parents=True, exist_ok=True)

    (s.MACROS_DIR / "three.json").write_text(
        json.dumps({"name": "three", "actions": [{"action": "a"}, {"action": "b"}, {"action": "c"}]}),
        encoding="utf-8",
    )
    (s.MACROS_DIR / "zero.json").write_text(json.dumps({"name": "zero", "actions": []}), encoding="utf-8")
    (s.MACROS_DIR / "missing.json").write_text(json.dumps({"name": "missing"}), encoding="utf-8")

    result = {entry["name"]: entry["action_count"] for entry in s.list_macros()}
    assert result["three"] == 3
    assert result["zero"] == 0
    assert result["missing"] == 0


def test_list_macros_path_field_is_absolute_string(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each entry's 'path' is the str(Path) of the on-disk JSON file."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    s.save_macro(recording_path=rec, name="pathed")

    [entry] = s.list_macros()
    assert entry["path"] == str(s.macro_path("pathed"))


def test_list_macros_includes_description_field(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The description from the JSON is propagated as-is (None preserved)."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    s.save_macro(recording_path=rec, name="hello", description="Howdy")
    s.save_macro(recording_path=rec, name="quiet")

    result = {entry["name"]: entry["description"] for entry in s.list_macros()}
    assert result["hello"] == "Howdy"
    assert result["quiet"] is None


def test_list_macros_parameters_default_empty_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """parameters default to [] when missing from the JSON."""
    s = _import_storage(monkeypatch, tmp_path)
    s.MACROS_DIR.mkdir(parents=True, exist_ok=True)
    (s.MACROS_DIR / "no-params.json").write_text(
        json.dumps({"name": "no-params", "actions": []}),
        encoding="utf-8",
    )
    [entry] = s.list_macros()
    assert entry["parameters"] == []


# ---------------------------------------------------------------------------
# load_macro
# ---------------------------------------------------------------------------


def test_load_macro_returns_full_dict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """load_macro() returns the parsed JSON object, including all top-level keys."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    s.save_macro(recording_path=rec, name="loadable", description="hi")

    loaded = s.load_macro("loadable")
    assert loaded["name"] == "loadable"
    assert loaded["description"] == "hi"
    assert "actions" in loaded


def test_load_macro_missing_error_includes_macro_name_quoted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The FileNotFoundError must quote the requested macro name with !r."""
    s = _import_storage(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError) as exc_info:
        s.load_macro("ghost")
    msg = str(exc_info.value)
    assert "'ghost'" in msg


def test_load_macro_missing_error_includes_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The FileNotFoundError mentions the resolved on-disk path."""
    s = _import_storage(monkeypatch, tmp_path)
    expected_path = str(s.macro_path("phantom"))
    with pytest.raises(FileNotFoundError) as exc_info:
        s.load_macro("phantom")
    assert expected_path in str(exc_info.value)


def test_load_macro_missing_error_includes_macro_list_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The error message hints at `macro_list` and `macro_save` for next steps."""
    s = _import_storage(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError) as exc_info:
        s.load_macro("nope")
    msg = str(exc_info.value)
    assert "macro_list" in msg
    assert "macro_save" in msg


def test_load_macro_missing_error_format_matches_template(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify the full prefix and the macro_list hint clause."""
    s = _import_storage(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError) as exc_info:
        s.load_macro("zzz")
    msg = str(exc_info.value)
    assert msg.startswith("no macro named 'zzz'")
    assert "; list saved macros with `macro_list`" in msg


# ---------------------------------------------------------------------------
# delete_macro
# ---------------------------------------------------------------------------


def test_delete_macro_returns_path_of_removed_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """delete_macro() returns the on-disk path that was unlinked."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    s.save_macro(recording_path=rec, name="goner")
    expected = s.macro_path("goner")
    out = s.delete_macro("goner")
    assert out == expected
    assert not expected.exists()


def test_delete_macro_missing_error_format(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """delete_macro() raises FileNotFoundError with the documented format."""
    s = _import_storage(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError) as exc_info:
        s.delete_macro("absent")
    msg = str(exc_info.value)
    assert msg.startswith("no macro named 'absent'")
    assert "; list saved macros with `macro_list`" in msg


def test_delete_macro_missing_error_includes_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """delete_macro() error mentions the path it expected to find."""
    s = _import_storage(monkeypatch, tmp_path)
    expected_path = str(s.macro_path("vapor"))
    with pytest.raises(FileNotFoundError) as exc_info:
        s.delete_macro("vapor")
    assert expected_path in str(exc_info.value)


def test_delete_macro_does_not_have_macro_save_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """delete_macro()'s error is shorter than load_macro's — no `macro_save` hint."""
    s = _import_storage(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError) as exc_info:
        s.delete_macro("missing")
    msg = str(exc_info.value)
    assert "macro_save" not in msg
    assert "record one" not in msg
