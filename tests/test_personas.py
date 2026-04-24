from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def fresh_personas(tmp_path, monkeypatch):
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


def test_load_persona_round_trip(tmp_path, fresh_personas):
    personas = fresh_personas
    _write_persona(tmp_path, "dante", {
        "name": "dante",
        "display_name": "Dante",
        "default_url": "https://example.com",
        "default_macros": ["login"],
        "credentials": {"email_env": "DANTE_EMAIL"},
        "app": {"discord_user_id": "1234", "role": "player"},
    })
    p = personas.load_persona("dante")
    assert p.name == "dante"
    assert p.display_name == "Dante"
    assert p.default_url == "https://example.com"
    assert p.default_macros == ["login"]
    assert p.credentials == {"email_env": "DANTE_EMAIL"}
    assert p.app == {"discord_user_id": "1234", "role": "player"}


def test_load_persona_missing_raises(fresh_personas):
    with pytest.raises(FileNotFoundError):
        fresh_personas.load_persona("ghost")


def test_load_persona_minimal(tmp_path, fresh_personas):
    _write_persona(tmp_path, "bare", {"name": "bare"})
    p = fresh_personas.load_persona("bare")
    assert p.name == "bare"
    assert p.display_name is None
    assert p.default_url is None
    assert p.default_macros == []
    assert p.credentials == {}
    assert p.app == {}
