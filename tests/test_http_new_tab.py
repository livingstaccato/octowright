# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Smoke tests for GET /new-tab — the default browser landing page."""

from __future__ import annotations

from starlette.testclient import TestClient

from octowright import http as _http


def _client() -> TestClient:
    return TestClient(_http.build_app())


def test_new_tab_returns_200() -> None:
    resp = _client().get("/new-tab")
    assert resp.status_code == 200


def test_new_tab_content_type_is_html() -> None:
    resp = _client().get("/new-tab")
    assert "text/html" in resp.headers["content-type"]


def test_new_tab_contains_otto_branding() -> None:
    resp = _client().get("/new-tab")
    assert "Octowright" in resp.text
    assert "browser ready" in resp.text


def test_new_tab_has_no_external_requests() -> None:
    """The page must be self-contained — no src/href pointing outside 127.0.0.1."""
    resp = _client().get("/new-tab")
    text = resp.text
    for external in ("https://", "http://fonts.", "cdn.", "googleapis"):
        assert external not in text, f"found external reference: {external}"
