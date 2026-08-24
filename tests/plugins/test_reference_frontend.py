# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The reference plugin's dashboard renderer, exercised end to end.

Every other plugin-frontend test (``test_plugins_api_route.py``,
``test_plugin_assets_route.py``) drives a FAKE descriptor, so nothing proves
that the reference plugin itself declares a working ``FrontendAsset`` the
real HTTP routes can serve. This module registers the REAL
``tests.plugins.reference.plugin.plugin`` and drives it through
``/api/plugins`` and the asset route, so a drift between ``renderer.js`` and
``plugin-contract.d.ts`` -- or a broken ``FrontendAsset`` declaration -- fails
CI here rather than in a third party's project months later.

Registers the plugin directly via ``PluginRegistry.register`` rather than the
full ``activate()`` pipeline: these assertions never touch a tool call, so
there is nothing to gain from also importing the plugin's tool module and
mutating the real ``mcp`` tool manager -- and everything to lose, since that
would need the heavier autouse cleanup ``test_reference_activation.py`` uses.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins import state as plugin_state
from octowright.plugins.registry import PluginRegistry
from tests.plugins.reference.plugin import plugin


class _Discovered:
    """Minimal stand-in carrying the entry-point NAME, which is the URL segment."""

    def __init__(self, name: str) -> None:
        self.name = name

    def status_row(self, state: str) -> dict[str, Any]:
        return {"name": self.name, "state": state}


ENTRY_POINT_NAME = "refkind-plugin"


def test_the_reference_plugin_declares_a_frontend_asset():
    frontend = plugin.frontend
    assert frontend is not None
    assert frontend.renderer_api_version == 1
    assert frontend.layout == "stream"
    assert frontend.asset_dir.is_dir()
    assert (frontend.asset_dir / frontend.module_path).is_file()


@pytest.fixture
def client(monkeypatch):
    from starlette.testclient import TestClient

    from octowright.http.app import build_app

    original = plugin_state.registry()
    registry = PluginRegistry()
    registry.register(plugin, pool=object(), adapter=None, discovered=_Discovered(ENTRY_POINT_NAME))
    plugin_state.set_registry(registry)
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "0")
    try:
        yield TestClient(build_app())
    finally:
        plugin_state.set_registry(original)


def test_api_plugins_lists_the_reference_kind_with_a_matching_descriptor(client):
    body = client.get("/api/plugins").json()
    assert plugin.kind in body
    row = body[plugin.kind]
    assert row["rendererApiVersion"] == plugin.frontend.renderer_api_version
    assert row["moduleUrl"] == f"/plugins/{ENTRY_POINT_NAME}/{plugin.frontend.module_path}"


def test_the_module_url_from_api_plugins_actually_serves_the_renderer(client):
    """Takes ``moduleUrl`` OUT of the ``/api/plugins`` response and fetches
    that exact URL, rather than hardcoding the path this test predicts -- so
    this proves the join between the two routes rather than re-asserting a
    value the test itself computed.
    """
    body = client.get("/api/plugins").json()
    module_url = body[plugin.kind]["moduleUrl"]

    resp = client.get(module_url)
    assert resp.status_code == 200
    on_disk = (plugin.frontend.asset_dir / plugin.frontend.module_path).read_text(encoding="utf-8")
    assert resp.text == on_disk
    assert "mountStream" in resp.text
