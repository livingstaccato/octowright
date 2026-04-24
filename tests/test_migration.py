from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def legacy_profiles(tmp_path, monkeypatch):
    """Sets up a legacy-layout PROFILES_DIR with profiles/<kind>/<name>/ dirs."""
    root = tmp_path
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(root))
    from octowright import defaults
    importlib.reload(defaults)
    from octowright import personas
    importlib.reload(personas)
    from octowright import profiles
    importlib.reload(profiles)

    # Legacy: PROFILES_DIR/<kind>/<name>/
    for kind in ("webkit", "chromium"):
        pdir = root / kind / "alice"
        pdir.mkdir(parents=True)
        (pdir / "Cookies").write_text("stub")
    (root / "webkit" / "bob").mkdir(parents=True)
    (root / "webkit" / "bob" / "Cookies").write_text("stub")
    return root, personas


def test_migrate_legacy_to_persona_first(legacy_profiles):
    root, personas = legacy_profiles
    summary = personas.migrate_legacy_layout()
    assert summary["moved"] == 3  # alice/webkit, alice/chromium, bob/webkit
    assert summary["personas"] == 2

    # New layout: PROFILES_DIR/<persona>/<kind>/ with profile.yaml
    assert (root / "alice" / "webkit" / "Cookies").exists()
    assert (root / "alice" / "chromium" / "Cookies").exists()
    assert (root / "alice" / "profile.yaml").exists()
    assert yaml.safe_load((root / "alice" / "profile.yaml").read_text())["name"] == "alice"
    assert (root / "bob" / "webkit" / "Cookies").exists()

    # Legacy dirs should be gone
    assert not (root / "webkit").exists()
    assert not (root / "chromium").exists()


def test_migrate_idempotent(legacy_profiles):
    root, personas = legacy_profiles
    personas.migrate_legacy_layout()
    summary = personas.migrate_legacy_layout()
    assert summary["moved"] == 0
    assert summary["personas"] == 0


def test_migrate_empty_dir_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path))
    from octowright import defaults
    importlib.reload(defaults)
    from octowright import personas
    importlib.reload(personas)
    summary = personas.migrate_legacy_layout()
    assert summary == {"moved": 0, "personas": 0}
