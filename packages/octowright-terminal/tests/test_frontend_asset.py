# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The plugin's declared renderer, and that it is actually on disk.

Mirrors tests/plugins/test_reference_frontend.py in core: a FrontendAsset whose
module_path does not exist produces a 404 in the dashboard and no error anywhere
else, so the declaration is checked against the filesystem.
"""

from __future__ import annotations

from octowright_terminal.plugin import plugin


def test_the_plugin_declares_a_frontend_asset():
    fa = plugin.frontend
    assert fa is not None
    assert fa.layout == "stream"
    assert fa.renderer_api_version == 1


def test_the_declared_module_exists_on_disk():
    fa = plugin.frontend
    assert fa.asset_dir.is_dir()
    assert (fa.asset_dir / fa.module_path).is_file()


def test_the_renderer_exports_mount_stream():
    fa = plugin.frontend
    source = (fa.asset_dir / fa.module_path).read_text(encoding="utf-8")
    assert "mountStream" in source
