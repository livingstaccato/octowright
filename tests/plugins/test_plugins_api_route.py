# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins import state as plugin_state
from octowright.plugins.contract import FrontendAsset
from octowright.plugins.registry import PluginRegistry


class _Descriptor:
    plugin_api_version = 1
    tool_names: frozenset[str] = frozenset()
    tool_module = None
    profile_name = None

    def __init__(self, kind: str, display_name: str, frontend: FrontendAsset | None) -> None:
        self.kind = kind
        self.display_name = display_name
        self.frontend = frontend

    def create_pool(self, ctx: Any) -> Any:
        raise AssertionError("not used")

    def create_scenario_adapter(self, pool: Any) -> Any:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


class _Discovered:
    def __init__(self, name: str) -> None:
        self.name = name

    def status_row(self, state: str) -> dict[str, Any]:
        return {"name": self.name, "state": state}


@pytest.fixture
def client_with(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from octowright.http.app import build_app

    def _build(*entries):
        original = plugin_state.registry()
        reg = PluginRegistry()
        for name, kind, display, frontend in entries:
            reg.register(
                _Descriptor(kind, display, frontend), pool=object(), adapter=None, discovered=_Discovered(name)
            )
        plugin_state.set_registry(reg)
        monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "0")
        return TestClient(build_app()), original

    yield _build
    # each test restores its own original inside the test body


def _asset(tmp_path, module_path="renderer.js", version=1, layout="stream"):
    d = tmp_path / "a"
    d.mkdir(exist_ok=True)
    return FrontendAsset(renderer_api_version=version, asset_dir=d, module_path=module_path, layout=layout)


def test_a_kind_with_a_frontend_is_listed(client_with, tmp_path):
    client, original = client_with(("my-plugin", "refkind", "Reference Kind", _asset(tmp_path)))
    try:
        body = client.get("/api/plugins").json()
        assert body["refkind"] == {
            "moduleUrl": "/plugins/my-plugin/renderer.js",
            "rendererApiVersion": 1,
            "displayName": "Reference Kind",
            "layout": "stream",
        }
    finally:
        plugin_state.set_registry(original)


def test_a_kind_without_a_frontend_is_absent(client_with, tmp_path):
    client, original = client_with(
        ("with-ui", "hasui", "Has UI", _asset(tmp_path)),
        ("no-ui", "noui", "No UI", None),
    )
    try:
        body = client.get("/api/plugins").json()
        assert "hasui" in body
        assert "noui" not in body, "a kind with no frontend must not appear at all"
    finally:
        plugin_state.set_registry(original)


def test_no_plugins_is_an_empty_object(client_with):
    client, original = client_with()
    try:
        assert client.get("/api/plugins").json() == {}
    finally:
        plugin_state.set_registry(original)


def test_module_url_is_built_from_the_entry_point_name(client_with, tmp_path):
    """The SPA never composes a plugin URL itself, so core owns this join."""
    client, original = client_with(("dash-named", "k", "K", _asset(tmp_path, module_path="dist/main.mjs")))
    try:
        body = client.get("/api/plugins").json()
        assert body["k"]["moduleUrl"] == "/plugins/dash-named/dist/main.mjs"
    finally:
        plugin_state.set_registry(original)
