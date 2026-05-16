# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch coverage for the persona loaders/scaffolders aimed at killing
mutmut survivors in ``load_persona``, ``list_personas``, and
``create_persona``. Companion to ``test_personas_branches.py`` (which
covers the helper functions). Each test asserts on a specific behaviour
so that mutating the underlying code produces an observable failure.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def fresh_personas(tmp_path, monkeypatch):
    """Same isolated PROFILES_DIR pattern used by tests/test_personas.py."""
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path))
    from octowright import defaults

    importlib.reload(defaults)
    from octowright import personas

    importlib.reload(personas)
    from octowright import profiles

    importlib.reload(profiles)
    return personas


def _write_persona(root: Path, name: str, doc: dict) -> None:
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "profile.yaml").write_text(yaml.safe_dump(doc))


# ---------------------------------------------------------------------------
# load_persona
# ---------------------------------------------------------------------------


def test_load_persona_missing_file_message_includes_name_and_path(fresh_personas):
    """Mutation: trimming the FileNotFoundError text would survive without this."""
    with pytest.raises(FileNotFoundError) as exc:
        fresh_personas.load_persona("ghost")
    msg = str(exc.value)
    assert "ghost" in msg
    assert "profile.yaml" in msg


def test_load_persona_non_dict_yaml_falls_back_to_defaults(tmp_path, fresh_personas):
    """A YAML doc that isn't a mapping (e.g. a list) collapses to an empty dict."""
    pdir = tmp_path / "weird"
    pdir.mkdir()
    (pdir / "profile.yaml").write_text(yaml.safe_dump(["not", "a", "mapping"]))
    p = fresh_personas.load_persona("weird")
    assert p.name == "weird"
    assert p.display_name is None
    assert p.default_url is None
    assert p.default_macros == []
    assert p.credentials == {}
    assert p.app == {}
    assert p.emoji is None


def test_load_persona_yaml_string_falls_back_to_defaults(tmp_path, fresh_personas):
    """A scalar YAML is also non-dict — same fallback path as the list case."""
    pdir = tmp_path / "scalar"
    pdir.mkdir()
    (pdir / "profile.yaml").write_text("just a string\n")
    p = fresh_personas.load_persona("scalar")
    assert p.name == "scalar"
    assert p.credentials == {}


def test_load_persona_uses_slug_when_name_field_absent(tmp_path, fresh_personas):
    """If yaml doesn't carry a ``name`` field, ``_slug(name_arg)`` fills it.

    persona_dir slugs the input arg, so we have to write the persona into the
    slugged directory the loader will look for.
    """
    pdir = tmp_path / "name-with-space"
    pdir.mkdir()
    (pdir / "profile.yaml").write_text(yaml.safe_dump({"display_name": "Test"}))
    p = fresh_personas.load_persona("name with space")
    assert p.name == "name-with-space"


def test_load_persona_explicit_yaml_name_overrides_slug(tmp_path, fresh_personas):
    """Mutation: dropping the .get('name', _slug(...)) default would still work
    when name is in yaml; this asserts the field is ACTUALLY read from yaml."""
    _write_persona(tmp_path, "dirslug", {"name": "yaml-supplied"})
    p = fresh_personas.load_persona("dirslug")
    assert p.name == "yaml-supplied"


def test_load_persona_emoji_field_round_trips(tmp_path, fresh_personas):
    """The emoji field has no default — None when absent, value when present."""
    _write_persona(tmp_path, "withemoji", {"name": "withemoji", "emoji": "🦊"})
    assert fresh_personas.load_persona("withemoji").emoji == "🦊"
    _write_persona(tmp_path, "noemoji", {"name": "noemoji"})
    assert fresh_personas.load_persona("noemoji").emoji is None


# ---------------------------------------------------------------------------
# list_personas
# ---------------------------------------------------------------------------


def test_list_personas_returns_empty_when_dir_missing(tmp_path, monkeypatch):
    """Mutation: dropping the existence guard would raise instead of returning []."""
    missing = tmp_path / "no-such-profiles-dir"
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(missing))
    from octowright import defaults

    importlib.reload(defaults)
    from octowright import personas

    importlib.reload(personas)
    assert personas.list_personas() == []


def test_list_personas_skips_files_at_top_level(tmp_path, fresh_personas):
    """A stray file in PROFILES_DIR is not a persona dir."""
    (tmp_path / "stray.txt").write_text("not a persona")
    _write_persona(tmp_path, "cosmo", {"name": "cosmo"})
    names = [p["name"] for p in fresh_personas.list_personas()]
    assert names == ["cosmo"]


def test_list_personas_skips_dirs_without_profile_yaml(tmp_path, fresh_personas):
    """A persona dir lacking ``profile.yaml`` is treated as orphan, not listed."""
    (tmp_path / "orphan").mkdir()
    _write_persona(tmp_path, "real", {"name": "real"})
    names = [p["name"] for p in fresh_personas.list_personas()]
    assert names == ["real"]


def test_list_personas_unparsable_yaml_logs_warning_and_keeps_entry(tmp_path, fresh_personas, monkeypatch):
    """A persona with broken YAML still appears in the list with display_name=None
    AND emits ``persona.yaml_parse_failed``. Mutation: dropping the warning
    would not break behaviour; this asserts the log is emitted via a spy on
    the module-level logger (provide.telemetry uses structlog, which doesn't
    always route through caplog)."""
    pdir = tmp_path / "broken"
    pdir.mkdir()
    # Deliberately unparsable YAML — unmatched bracket plus a tab makes
    # PyYAML's safe loader raise (a plain string would parse to a scalar).
    (pdir / "profile.yaml").write_text("\t- [unclosed\n  bad: \tindent: nope\n")

    captured: list[tuple[str, dict[str, Any]]] = []

    class _SpyLog:
        def warning(self, event: str, **kwargs: Any) -> None:
            captured.append((event, kwargs))

        def __getattr__(self, _name: str):
            return lambda *a, **k: None

    monkeypatch.setattr(fresh_personas, "log", _SpyLog())
    listing = fresh_personas.list_personas()
    names = [p["name"] for p in listing]
    assert "broken" in names
    entry = next(p for p in listing if p["name"] == "broken")
    assert entry["display_name"] is None
    assert any(event == "persona.yaml_parse_failed" for event, _ in captured)


def test_list_personas_engines_only_includes_supported_kinds(tmp_path, fresh_personas):
    """Mutation: removing the SUPPORTED_KINDS filter would surface stray dirs as engines."""
    pdir = tmp_path / "cosmo"
    pdir.mkdir()
    (pdir / "profile.yaml").write_text(yaml.safe_dump({"name": "cosmo"}))
    (pdir / "chromium").mkdir()
    (pdir / "firefox").mkdir()
    (pdir / "webkit").mkdir()
    (pdir / "trash").mkdir()
    entry = fresh_personas.list_personas()[0]
    assert entry["engines"] == ["chromium", "firefox", "webkit"]


def test_list_personas_skips_files_inside_persona_dir(tmp_path, fresh_personas):
    """Files inside the persona dir aren't engines."""
    pdir = tmp_path / "cosmo"
    pdir.mkdir()
    (pdir / "profile.yaml").write_text(yaml.safe_dump({"name": "cosmo"}))
    (pdir / "chromium").mkdir()
    (pdir / "notes.txt").write_text("hi")
    entry = fresh_personas.list_personas()[0]
    assert entry["engines"] == ["chromium"]


def test_list_personas_sorts_most_recent_mtime_first(tmp_path, fresh_personas):
    """Non-pinned personas still sort most-recent-first."""
    import os
    import time

    _write_persona(tmp_path, "old", {"name": "old"})
    time.sleep(0.05)
    _write_persona(tmp_path, "newer", {"name": "newer"})
    time.sleep(0.05)
    _write_persona(tmp_path, "newest", {"name": "newest"})
    now = time.time()
    os.utime(tmp_path / "old", (now - 100, now - 100))
    os.utime(tmp_path / "newer", (now - 50, now - 50))
    os.utime(tmp_path / "newest", (now, now))

    listing = fresh_personas.list_personas()
    names = [p["name"] for p in listing]
    assert names == ["newest", "newer", "old"]


def test_list_personas_pins_dante_and_tim_first(tmp_path, fresh_personas):
    """Dante and Tim are first-party examples and should stay easy to find."""
    import os
    import time

    _write_persona(tmp_path, "newest", {"name": "newest"})
    _write_persona(tmp_path, "tim", {"name": "tim", "display_name": "Tanooki Tim"})
    _write_persona(tmp_path, "dante", {"name": "dante", "display_name": "Dinosaur Dante"})
    now = time.time()
    os.utime(tmp_path / "dante", (now - 300, now - 300))
    os.utime(tmp_path / "tim", (now - 200, now - 200))
    os.utime(tmp_path / "newest", (now, now))

    names = [p["name"] for p in fresh_personas.list_personas()]
    assert names == ["dante", "tim", "newest"]


def test_list_personas_last_used_iso_format_matches_mtime(tmp_path, fresh_personas):
    """Mutation: removing the ``replace('+00:00', 'Z')`` would surface +00:00."""
    _write_persona(tmp_path, "cosmo", {"name": "cosmo"})
    entry = fresh_personas.list_personas()[0]
    assert entry["last_used"].endswith("Z")
    assert "+00:00" not in entry["last_used"]


# ---------------------------------------------------------------------------
# create_persona
# ---------------------------------------------------------------------------


def test_create_persona_minimal_writes_yaml_and_returns_dir(tmp_path, fresh_personas):
    """Mutation: returning the wrong path or skipping the yaml write would fail."""
    pdir = fresh_personas.create_persona("cosmo")
    assert pdir == tmp_path / "cosmo"
    assert (pdir / "profile.yaml").exists()
    doc = yaml.safe_load((pdir / "profile.yaml").read_text())
    assert doc == {"name": "cosmo"}


def test_create_persona_with_display_name_and_default_url(tmp_path, fresh_personas):
    """Mutation: dropping the ``if display_name`` / ``if default_url`` branch
    would either always include or always exclude the fields."""
    pdir = fresh_personas.create_persona("ziggy", display_name="Zazzle Ziggy", default_url="https://example.com")
    doc = yaml.safe_load((pdir / "profile.yaml").read_text())
    assert doc["display_name"] == "Zazzle Ziggy"
    assert doc["default_url"] == "https://example.com"


def test_create_persona_existing_yaml_raises_with_path(tmp_path, fresh_personas):
    """Mutation: removing the FileExistsError raise would silently overwrite."""
    fresh_personas.create_persona("cosmo")
    with pytest.raises(FileExistsError) as exc:
        fresh_personas.create_persona("cosmo")
    assert "cosmo" in str(exc.value)
    assert "profile.yaml" in str(exc.value)


def test_create_persona_slugs_the_name_field(tmp_path, fresh_personas):
    """The yaml doc's name is the slugged form, not the raw input."""
    pdir = fresh_personas.create_persona("cosmo and ziggy")
    doc = yaml.safe_load((pdir / "profile.yaml").read_text())
    assert doc["name"] == "cosmo-and-ziggy"
    assert pdir.name == "cosmo-and-ziggy"


def test_create_persona_empty_display_name_omits_field(tmp_path, fresh_personas):
    """``if display_name:`` is falsy for empty string — field should NOT be written."""
    pdir = fresh_personas.create_persona("cosmo", display_name="")
    doc = yaml.safe_load((pdir / "profile.yaml").read_text())
    assert "display_name" not in doc
