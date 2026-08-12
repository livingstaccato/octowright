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
    p.name = "cosmo"
    p.display_name = "Crumpet Cosmo"
    p.default_url = "https://octowright.com"
    p.default_macros = ["login"]
    p.credentials = {"email_env": "COSMO_EMAIL"}
    p.app = {"team": "qa"}
    _patch_deps["persona"].load_persona.return_value = p
    out = _personas.persona_get("cosmo")
    assert out["name"] == "cosmo"
    assert out["credentials"]["email_env"] == "COSMO_EMAIL"


async def test_profile_delete_refuses_when_in_use(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["pool"].profile_in_use.return_value = True
    _patch_deps["pool"].list_sessions.return_value = [{"instance_id": "i-1", "kind": "webkit", "profile": "cosmo"}]
    with pytest.raises(RuntimeError):
        await _personas.profile_delete("webkit", "cosmo")


async def test_profile_delete_refusal_matches_slug_alias(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["pool"].profile_in_use.return_value = True
    _patch_deps["pool"].list_sessions.return_value = [
        {"instance_id": "i-alias", "kind": "webkit", "profile": "cosmo one"}
    ]
    with pytest.raises(RuntimeError, match="i-alias"):
        await _personas.profile_delete("webkit", "cosmo-one")


async def test_profile_delete_success(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["pool"].profile_in_use.return_value = False
    _patch_deps["profile"].delete_profile.return_value = "/tmp/prof"
    out = await _personas.profile_delete("chromium", "ziggy")
    assert out["deleted"] is True


def test_persona_create_exists_maps_to_runtime_error(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["persona"].create_persona.side_effect = FileExistsError("exists")
    with pytest.raises(RuntimeError):
        _personas.persona_create("cosmo")


def test_persona_create_success(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["persona"].create_persona.return_value = "/tmp/persona"
    out = _personas.persona_create("new", display_name="New")
    assert out["created"] is True


async def test_persona_delete_refuses_live_instance(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["pool"].list_sessions.return_value = [{"instance_id": "i-2", "profile": "cosmo"}]
    with pytest.raises(RuntimeError):
        await _personas.persona_delete("cosmo")


async def test_persona_delete_refuses_live_slug_alias(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["pool"].list_sessions.return_value = [{"instance_id": "i-alias", "profile": "cosmo one"}]
    with pytest.raises(RuntimeError, match="i-alias"):
        await _personas.persona_delete("cosmo-one")


async def test_persona_delete_success(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["pool"].list_sessions.return_value = []
    _patch_deps["profile"].delete_persona.return_value = "/tmp/cosmo"
    out = await _personas.persona_delete("cosmo")
    assert out["deleted"] is True


async def test_profile_delete_waits_for_profile_lifecycle_lock(_patch_deps: dict[str, MagicMock]) -> None:
    import asyncio

    from octowright.profile_lifecycle import profile_lifecycle_lock

    _patch_deps["pool"].profile_in_use.return_value = False
    _patch_deps["profile"].delete_profile.return_value = "/tmp/prof"

    async with profile_lifecycle_lock("chromium", "cosmo"):
        delete_task = asyncio.create_task(_personas.profile_delete("chromium", "cosmo"))
        await asyncio.sleep(0)
        _patch_deps["profile"].delete_profile.assert_not_called()

    out = await delete_task
    assert out["deleted"] is True


async def test_persona_delete_waits_for_every_engine_profile_lock(_patch_deps: dict[str, MagicMock]) -> None:
    import asyncio

    from octowright.profile_lifecycle import profile_lifecycle_lock

    _patch_deps["pool"].list_sessions.return_value = []
    _patch_deps["profile"].delete_persona.return_value = "/tmp/cosmo"

    async with profile_lifecycle_lock("firefox", "cosmo"):
        delete_task = asyncio.create_task(_personas.persona_delete("cosmo"))
        await asyncio.sleep(0)
        _patch_deps["profile"].delete_persona.assert_not_called()

    out = await delete_task
    assert out["deleted"] is True


async def test_persona_list_and_credentials_check(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["persona"].list_personas.return_value = [{"name": "cosmo"}]
    out = _personas.persona_list()
    assert out == [{"name": "cosmo"}]
    p = MagicMock()
    _patch_deps["persona"].load_persona.return_value = p
    _patch_deps["persona"].check_credentials.return_value = {"ok": True}
    checked = await _personas.persona_credentials_check("cosmo")
    assert checked["ok"] is True
