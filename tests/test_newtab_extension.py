# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the Chromium new-tab override extension generator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_ensure_newtab_extension_writes_valid_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.browser_pool.newtab_extension as ext

    monkeypatch.setattr(ext, "_EXTENSION_DIR", tmp_path / "newtab-extension")
    d = ext.ensure_newtab_extension("http://127.0.0.1:6286/new-tab")

    manifest = json.loads((d / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    assert manifest["chrome_url_overrides"]["newtab"] == "newtab.html"


def test_ensure_newtab_extension_bakes_url_into_external_js(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.browser_pool.newtab_extension as ext

    monkeypatch.setattr(ext, "_EXTENSION_DIR", tmp_path / "ne")
    d = ext.ensure_newtab_extension("http://127.0.0.1:6286/new-tab")

    # Redirect must live in an external JS file (MV3 CSP blocks inline scripts).
    js = (d / "newtab.js").read_text()
    assert "http://127.0.0.1:6286/new-tab" in js
    assert "location.replace" in js
    html = (d / "newtab.html").read_text()
    assert '<script src="newtab.js"></script>' in html
    assert "location.replace" not in html  # not inline


def test_ensure_newtab_extension_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.browser_pool.newtab_extension as ext

    monkeypatch.setattr(ext, "_EXTENSION_DIR", tmp_path / "ne")
    d1 = ext.ensure_newtab_extension("http://x/new-tab")
    d2 = ext.ensure_newtab_extension("http://x/new-tab")
    assert d1 == d2
    assert (d1 / "manifest.json").exists()
