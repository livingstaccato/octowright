# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from octowright.server import personas as _personas


@pytest.fixture(autouse=True)
def _patch_deps(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    fake_pool = MagicMock()
    fake_profile = MagicMock()
    fake_persona = MagicMock()
    monkeypatch.setattr(_personas, "pool", fake_pool)
    monkeypatch.setattr(_personas, "profile_mod", fake_profile)
    monkeypatch.setattr(_personas, "persona_mod", fake_persona)
    return {"pool": fake_pool, "profile": fake_profile, "persona": fake_persona}


def test_profile_list_forwards_kind(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["profile"].list_profiles.return_value = [{"kind": "webkit", "name": "a"}]
    out = _personas.profile_list("webkit")
    assert out == [{"kind": "webkit", "name": "a"}]


def test_persona_get_maps_fields(_patch_deps: dict[str, MagicMock]) -> None:
    p = MagicMock()
    p.name = "alice"
    p.display_name = "Alice"
    p.default_url = "https://example.com"
    p.default_macros = ["login"]
    p.credentials = {"email_env": "ALICE_EMAIL"}
    p.app = {"team": "qa"}
    _patch_deps["persona"].load_persona.return_value = p
    out = _personas.persona_get("alice")
    assert out["name"] == "alice"
    assert out["credentials"]["email_env"] == "ALICE_EMAIL"
