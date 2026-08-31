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
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names: frozenset[str] = frozenset()
    tool_module = None
    profile_name = None

    def __init__(self, frontend: FrontendAsset | None) -> None:
        self.frontend = frontend

    def create_pool(self, ctx: Any) -> Any:
        raise AssertionError("not used")

    def create_scenario_adapter(self, pool: Any) -> Any:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


class _Discovered:
    """Minimal stand-in carrying the entry-point NAME, which is the URL segment."""

    def __init__(self, name: str) -> None:
        self.name = name

    def status_row(self, state: str) -> dict[str, Any]:
        return {"name": self.name, "state": state}


@pytest.fixture
def served(tmp_path, monkeypatch):
    """Register one plugin whose assets live in a real directory on disk."""
    from starlette.testclient import TestClient

    from octowright.http.app import build_app

    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    (asset_dir / "renderer.js").write_text("export function mountStream() {}\n", encoding="utf-8")
    (asset_dir / "style.css").write_text(".x{}\n", encoding="utf-8")
    nested = asset_dir / "sub"
    nested.mkdir()
    (nested / "deep.js").write_text("//deep\n", encoding="utf-8")

    original = plugin_state.registry()
    reg = PluginRegistry()
    frontend = FrontendAsset(renderer_api_version=1, asset_dir=asset_dir, module_path="renderer.js", layout="stream")
    reg.register(_Descriptor(frontend), pool=object(), adapter=None, discovered=_Discovered("my-plugin"))
    plugin_state.set_registry(reg)
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "0")
    try:
        yield TestClient(build_app()), asset_dir
    finally:
        plugin_state.set_registry(original)


def test_a_declared_asset_is_served(served):
    client, _ = served
    resp = client.get("/plugins/my-plugin/renderer.js")
    assert resp.status_code == 200
    assert "mountStream" in resp.text


def test_a_nested_asset_is_served(served):
    client, _ = served
    assert client.get("/plugins/my-plugin/sub/deep.js").status_code == 200


def test_a_served_asset_is_not_a_forced_download(served):
    """A plugin renderer's only real consumer is `import()`, which ignores
    Content-Disposition -- but opening the module URL directly to debug it
    (or a stray browser navigation to it) must show the module, not download
    it. FileResponse only sets Content-Disposition when given `filename=`, so
    this pins that the route never passes one.
    """
    client, _ = served
    resp = client.get("/plugins/my-plugin/renderer.js")
    assert resp.status_code == 200
    assert "content-disposition" not in resp.headers


def test_an_unknown_plugin_name_is_404(served):
    client, _ = served
    assert client.get("/plugins/nosuchplugin/renderer.js").status_code == 404


def test_a_plugin_with_no_frontend_is_404(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from octowright.http.app import build_app

    original = plugin_state.registry()
    reg = PluginRegistry()
    reg.register(_Descriptor(None), pool=object(), adapter=None, discovered=_Discovered("bare"))
    plugin_state.set_registry(reg)
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "0")
    try:
        client = TestClient(build_app())
        assert client.get("/plugins/bare/renderer.js").status_code == 404
    finally:
        plugin_state.set_registry(original)


@pytest.mark.parametrize("path", ["../secret.txt", "sub/../../secret.txt", "..%2Fsecret.txt"])
def test_traversal_is_refused(served, tmp_path, path):
    """NOTE on what this actually exercises: the two literal-``../`` cases
    never reach containment. httpx2 (the test client) performs RFC 3986
    dot-segment normalization client-side before the request is even sent, so
    ``/plugins/my-plugin/../secret.txt`` is rewritten to ``/plugins/secret.txt``
    ahead of the route match; that 404s because no plugin is registered under
    the name ``secret.txt``, not because ``reject_unsafe_path`` caught anything.
    Only the percent-encoded case (``..%2Fsecret.txt``) survives client-side
    normalization and reaches the handler with a literal ``..`` path segment --
    and even that one is stopped by the closed suffix allowlist (``.txt`` is
    not served) before ``reject_unsafe_path`` is ever called, since this
    target's extension isn't in the allowlist. These cases still pin real,
    desired behavior (a plugin name that happens to look like an escaped path
    component must never leak a file), but none of them is evidence that the
    resolve-then-contain check itself works -- see
    ``test_a_dot_segment_escape_with_an_allowed_suffix_is_stopped_by_containment``
    below for that.
    """
    client, _asset_dir = served
    (tmp_path / "secret.txt").write_text("do not serve me", encoding="utf-8")
    resp = client.get(f"/plugins/my-plugin/{path}")
    assert resp.status_code in (400, 404)
    assert "do not serve me" not in resp.text


def test_a_symlink_escaping_the_asset_dir_is_refused(served, tmp_path):
    client, asset_dir = served
    outside = tmp_path / "outside.js"
    outside.write_text("escaped", encoding="utf-8")
    (asset_dir / "escape.js").symlink_to(outside)
    resp = client.get("/plugins/my-plugin/escape.js")
    assert resp.status_code in (400, 404)
    assert "escaped" not in resp.text


def test_a_dot_segment_escape_with_an_allowed_suffix_is_stopped_by_containment(served, tmp_path):
    """Plants the escape target with an ALLOWED suffix (``.js``), so unlike
    every ``.txt`` case in ``test_traversal_is_refused`` above, the closed
    suffix allowlist cannot be what stops this one -- only
    ``reject_unsafe_path``'s resolve-then-contain check can. Uses the
    percent-encoded ``..%2F`` form because a literal ``../`` is collapsed by
    the test client's own URL normalization before the request is sent (see
    the note above); the encoded slash survives that normalization and is
    decoded to a literal ``..`` path segment by Starlette's ``:path`` route
    converter, so this is the request that genuinely reaches
    ``reject_unsafe_path`` with a dot-segment. Asserts the precise error the
    containment check raises, not just a generic 404, so a regression in
    ``reject_unsafe_path``'s dot-segment handling can't hide behind the
    suffix allowlist or the plugin-name lookup the way it could in the older
    parametrized cases.
    """
    client, _asset_dir = served
    (tmp_path / "secret.js").write_text("do not serve me either", encoding="utf-8")
    resp = client.get("/plugins/my-plugin/..%2Fsecret.js")
    assert resp.status_code == 404
    assert "do not serve me either" not in resp.text
    assert resp.json()["error"] == "asset path escapes the plugin's asset dir"


def test_a_missing_file_under_a_valid_plugin_is_404(served):
    client, _ = served
    assert client.get("/plugins/my-plugin/nope.js").status_code == 404
