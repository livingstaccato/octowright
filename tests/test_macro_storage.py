# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-precise tests for octowright.macros.storage primitives.

Targets the slug / macro_path / now_iso / save_macro / write_macro slice of
the 88 mutmut survivors. Lookup/listing/deletion live in
test_macro_storage_query.py.
"""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path
from typing import Any

import pytest


def _import_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Import storage with MACROS_DIR pinned at tmp_path/macros.

    MACROS_DIR is owned by octowright.defaults, so reload defaults first
    to pick up the env var, then reload storage to refresh its
    `MACROS_DIR = defaults.MACROS_DIR` re-export.
    """
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros.storage as _storage

    importlib.reload(_storage)
    return _storage


def _write_recording(tmp_path: Path, lines: list[dict[str, Any]] | None = None) -> Path:
    """Write a JSONL recording file with the supplied (or default) entries."""
    p = tmp_path / "recording.jsonl"
    rows = lines or [
        {"ts": "2026-04-24T10:00:00Z", "action": "navigate", "url": "https://octowright.com"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# slug
# ---------------------------------------------------------------------------


def test_slug_replaces_special_chars_with_dash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """slug() must replace runs of non-[A-Za-z0-9._-] with a single dash."""
    s = _import_storage(monkeypatch, tmp_path)
    assert s.slug("hello world") == "hello-world"
    assert s.slug("foo  bar") == "foo-bar"
    assert s.slug("foo!!bar") == "foo-bar"


def test_slug_strips_leading_and_trailing_dashes_and_dots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """slug() must strip leading/trailing dashes and dots after substitution."""
    s = _import_storage(monkeypatch, tmp_path)
    assert s.slug("--hello--") == "hello"
    assert s.slug("..hello..") == "hello"
    assert s.slug(" -.hello.- ") == "hello"


def test_slug_preserves_mixed_case(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """slug() must not lowercase its input — case is preserved as-is."""
    s = _import_storage(monkeypatch, tmp_path)
    assert s.slug("DiscordLogin") == "DiscordLogin"
    assert s.slug("MixedCASE") == "MixedCASE"


def test_slug_preserves_dots_underscores_dashes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """slug() leaves dots, underscores, and dashes intact when interior."""
    s = _import_storage(monkeypatch, tmp_path)
    assert s.slug("hello.world") == "hello.world"
    assert s.slug("hello_world") == "hello_world"
    assert s.slug("hello-world") == "hello-world"


def test_slug_strips_surrounding_whitespace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """slug() calls .strip() before substitution — outer whitespace gone."""
    s = _import_storage(monkeypatch, tmp_path)
    assert s.slug("  hello  ") == "hello"
    assert s.slug("\thello\n") == "hello"


def test_slug_raises_on_empty_input(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """slug() raises ValueError including the original repr when result is empty."""
    s = _import_storage(monkeypatch, tmp_path)
    with pytest.raises(ValueError) as exc_info:
        s.slug("")
    msg = str(exc_info.value)
    assert "empty slug" in msg
    assert "''" in msg


def test_slug_raises_on_all_special_chars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """slug() raises when input is all special chars (collapse to nothing)."""
    s = _import_storage(monkeypatch, tmp_path)
    with pytest.raises(ValueError) as exc_info:
        s.slug("!!!")
    assert "empty slug" in str(exc_info.value)
    assert "'!!!'" in str(exc_info.value)


def test_slug_raises_on_all_dashes_and_dots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All-dash / all-dot input strips to empty and raises ValueError."""
    s = _import_storage(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        s.slug("---")
    with pytest.raises(ValueError):
        s.slug("...")
    with pytest.raises(ValueError):
        s.slug("-.-")


def test_slug_raises_on_only_whitespace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All-whitespace input collapses to empty and raises."""
    s = _import_storage(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        s.slug("   ")


# ---------------------------------------------------------------------------
# macro_path
# ---------------------------------------------------------------------------


def test_macro_path_returns_json_under_macros_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """macro_path() returns MACROS_DIR / '<slug>.json' — exact suffix and parent."""
    s = _import_storage(monkeypatch, tmp_path)
    p = s.macro_path("login")
    assert p.parent == s.MACROS_DIR
    assert p.name == "login.json"
    assert p.suffix == ".json"


def test_macro_path_uses_slugged_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """macro_path() applies slug() to its argument before concatenating .json."""
    s = _import_storage(monkeypatch, tmp_path)
    assert s.macro_path("hello world").name == "hello-world.json"
    assert s.macro_path("foo!!bar").name == "foo-bar.json"


def test_macro_path_propagates_slug_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """macro_path() raises ValueError when slug rejects the input."""
    s = _import_storage(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        s.macro_path("")


def test_macro_path_enforces_containment_even_when_slug_returns_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """macro_path() is the security boundary, not slug().

    The current slug regex collapses ``/`` into ``-`` so the obvious LLM
    payload (``"../../../etc/passwd"``) is already neutralised at the slug
    stage. Defense-in-depth: macro_path() must still reject anything that
    resolves outside MACROS_DIR, even if a future slug change leaks a
    traversal string through. Verify that with a stubbed slug.
    """
    s = _import_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(s, "slug", lambda name: "../etc/passwd")
    with pytest.raises(ValueError, match="resolves outside"):
        s.macro_path("anything")


def test_macro_path_enforces_containment_against_absolute_slug(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An absolute-path slug must also be rejected — Path('/abs') / '...' yields '/abs'."""
    s = _import_storage(monkeypatch, tmp_path)
    other = tmp_path / "other-dir"
    other.mkdir()
    monkeypatch.setattr(s, "slug", lambda name: str(other / "evil"))
    with pytest.raises(ValueError, match="resolves outside"):
        s.macro_path("anything")


# ---------------------------------------------------------------------------
# now_iso
# ---------------------------------------------------------------------------


def test_now_iso_ends_with_capital_z(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """now_iso() must end in 'Z', not '+00:00'."""
    s = _import_storage(monkeypatch, tmp_path)
    out = s.now_iso()
    assert out.endswith("Z"), out
    assert "+00:00" not in out


def test_now_iso_does_not_contain_plus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """now_iso() output never contains '+' (the offset got stripped)."""
    s = _import_storage(monkeypatch, tmp_path)
    out = s.now_iso()
    assert "+" not in out


def test_now_iso_is_iso8601_shaped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """now_iso() output is YYYY-MM-DDTHH:MM:SS[.ffffff]Z."""
    s = _import_storage(monkeypatch, tmp_path)
    out = s.now_iso()
    assert out[4] == "-"
    assert out[7] == "-"
    assert out[10] == "T"
    assert out[13] == ":"
    assert out[16] == ":"


# ---------------------------------------------------------------------------
# save_macro — on-disk shape
# ---------------------------------------------------------------------------


def test_save_macro_returns_dest_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save_macro() returns the path it wrote to — equal to macro_path(name)."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    out = s.save_macro(recording_path=rec, name="my-macro")
    assert out == s.macro_path("my-macro")
    assert out.exists()


def test_save_macro_writes_indented_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save_macro() writes JSON with indent=2 — newlines and two-space indent present."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    p = s.save_macro(recording_path=rec, name="indent-check")
    raw = p.read_text(encoding="utf-8")
    assert "\n" in raw
    assert '  "name"' in raw


def test_save_macro_uses_ensure_ascii_false(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save_macro() preserves non-ASCII chars literally (ensure_ascii=False)."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    p = s.save_macro(recording_path=rec, name="utf8", description="café — 日本語")
    raw = p.read_text(encoding="utf-8")
    assert "café — 日本語" in raw
    assert "\\u" not in raw


def test_save_macro_writes_utf8_encoding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save_macro() uses utf-8 encoding when writing."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    p = s.save_macro(recording_path=rec, name="utf8-write", description="日本語")
    raw_bytes = p.read_bytes()
    assert "日本語".encode() in raw_bytes


def test_save_macro_creates_dir_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save_macro() creates MACROS_DIR (parents=True, exist_ok=True) when absent."""
    s = _import_storage(monkeypatch, tmp_path)
    assert not s.MACROS_DIR.exists()

    rec = _write_recording(tmp_path)
    s.save_macro(recording_path=rec, name="dir-create")
    assert s.MACROS_DIR.exists()
    assert s.MACROS_DIR.is_dir()


def test_save_macro_creates_intermediate_parent_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save_macro() creates parents=True so deep MACROS_DIR paths work."""
    deep = tmp_path / "deep" / "nested"
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(deep))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros.storage as _storage

    importlib.reload(_storage)
    rec = _write_recording(tmp_path)
    out = _storage.save_macro(recording_path=rec, name="deep-write")
    assert out.exists()
    assert deep.exists()


def test_save_macro_top_level_keys_exact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save_macro() writes exactly these keys: name, description, parameters, created_at, updated_at, actions."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    p = s.save_macro(recording_path=rec, name="key-check")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"name", "description", "parameters", "created_at", "updated_at", "actions"}


def test_save_macro_description_default_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When no description is passed, description is None — not '' or missing."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    p = s.save_macro(recording_path=rec, name="no-desc")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["description"] is None


def test_save_macro_parameters_default_is_empty_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When no parameters passed, parameters is [] — not None or missing."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    p = s.save_macro(recording_path=rec, name="no-params")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["parameters"] == []


def test_save_macro_parameters_order_matches_dict_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """parameters list preserves dict insertion order (Python 3.7+ guarantee)."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    p = s.save_macro(
        recording_path=rec,
        name="order-test",
        parameters={"zulu": "z", "alpha": "a", "mike": "m"},
    )
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["parameters"] == ["zulu", "alpha", "mike"]


def test_save_macro_parameters_list_input_creates_indexed_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """List parameters become params[0], params[1], ... per normalise_parameters."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    p = s.save_macro(recording_path=rec, name="list-params", parameters=["foo", "bar"])
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["parameters"] == ["params[0]", "params[1]"]


def test_save_macro_created_at_preserved_across_resave(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Re-saving the same macro keeps created_at; only updated_at advances."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)

    s.save_macro(recording_path=rec, name="resave")
    first = json.loads(s.macro_path("resave").read_text())
    time.sleep(0.01)
    s.save_macro(recording_path=rec, name="resave")
    second = json.loads(s.macro_path("resave").read_text())

    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] >= first["updated_at"]


def test_save_macro_updated_at_advances_on_resave(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """updated_at on the second save is strictly >= the first (monotonic)."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    s.save_macro(recording_path=rec, name="advance")
    first_updated = json.loads(s.macro_path("advance").read_text())["updated_at"]
    time.sleep(0.05)
    s.save_macro(recording_path=rec, name="advance")
    second_updated = json.loads(s.macro_path("advance").read_text())["updated_at"]
    assert second_updated >= first_updated


def test_save_macro_falls_back_when_existing_is_corrupt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the existing macro JSON is malformed, save_macro recovers and writes fresh created_at."""
    s = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    s.MACROS_DIR.mkdir(parents=True, exist_ok=True)
    s.macro_path("corrupt").write_text("{not valid json", encoding="utf-8")

    out = s.save_macro(recording_path=rec, name="corrupt")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["created_at"].endswith("Z")
    assert data["name"] == "corrupt"


# ---------------------------------------------------------------------------
# write_macro
# ---------------------------------------------------------------------------


def test_write_macro_returns_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """write_macro() returns macro_path(name)."""
    s = _import_storage(monkeypatch, tmp_path)
    out = s.write_macro(name="written", macro={"actions": []})
    assert out == s.macro_path("written")
    assert out.exists()


def test_write_macro_overrides_name_in_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """write_macro() overwrites whatever name is in the payload with the kwarg name."""
    s = _import_storage(monkeypatch, tmp_path)
    out = s.write_macro(name="real-name", macro={"name": "wrong-name", "actions": []})
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["name"] == "real-name"


def test_write_macro_defaults_created_at_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """write_macro() injects created_at when not provided in the payload."""
    s = _import_storage(monkeypatch, tmp_path)
    out = s.write_macro(name="fresh", macro={"actions": []})
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["created_at"].endswith("Z")


def test_write_macro_preserves_provided_created_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """write_macro() does NOT overwrite created_at when the payload supplies it."""
    s = _import_storage(monkeypatch, tmp_path)
    custom = "2020-01-01T00:00:00Z"
    out = s.write_macro(name="kept-ts", macro={"actions": [], "created_at": custom})
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["created_at"] == custom


def test_write_macro_always_overwrites_updated_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """write_macro() ALWAYS sets updated_at to now — payload value is replaced."""
    s = _import_storage(monkeypatch, tmp_path)
    stale = "1999-01-01T00:00:00Z"
    out = s.write_macro(name="refreshed-ts", macro={"actions": [], "updated_at": stale})
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["updated_at"] != stale
    assert data["updated_at"].endswith("Z")


def test_write_macro_does_not_mutate_caller_dict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """write_macro() deep-copies the input dict — caller's macro is unchanged."""
    s = _import_storage(monkeypatch, tmp_path)
    incoming = {"actions": [{"action": "navigate", "url": "https://x"}], "extra": "kept"}
    snapshot = json.dumps(incoming, sort_keys=True)
    s.write_macro(name="immutable-input", macro=incoming)
    assert json.dumps(incoming, sort_keys=True) == snapshot
    assert "name" not in incoming
    assert "created_at" not in incoming
    assert "updated_at" not in incoming


def test_write_macro_creates_parent_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """write_macro() creates MACROS_DIR (parents=True) when absent."""
    deep = tmp_path / "a" / "b" / "c"
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(deep))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros.storage as _storage

    importlib.reload(_storage)
    out = _storage.write_macro(name="deep-write", macro={"actions": []})
    assert out.exists()
    assert deep.is_dir()


def test_write_macro_writes_with_indent_and_utf8(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """write_macro() serialises with indent=2, ensure_ascii=False, utf-8 encoding."""
    s = _import_storage(monkeypatch, tmp_path)
    out = s.write_macro(name="utf8-write", macro={"actions": [], "note": "café 日本語"})
    raw = out.read_text(encoding="utf-8")
    assert "\n" in raw
    assert "café 日本語" in raw
    assert "\\u" not in raw


def test_write_macro_keeps_extra_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Arbitrary extra keys in the payload are preserved on disk."""
    s = _import_storage(monkeypatch, tmp_path)
    out = s.write_macro(name="extra-keys", macro={"actions": [], "custom": {"k": 1}})
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["custom"] == {"k": 1}
