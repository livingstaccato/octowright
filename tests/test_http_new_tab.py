# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Smoke tests for GET /new-tab and GET /otto.svg."""

from __future__ import annotations

from starlette.testclient import TestClient

from octowright import http as _http


def _client() -> TestClient:
    return TestClient(_http.build_app())


def test_new_tab_returns_200() -> None:
    assert _client().get("/new-tab").status_code == 200


def test_new_tab_content_type_is_html() -> None:
    assert "text/html" in _client().get("/new-tab").headers["content-type"]


def test_new_tab_contains_wordmark() -> None:
    text = _client().get("/new-tab").text
    assert "octowright" in text.lower()


def test_new_tab_wordmark_is_lowercase() -> None:
    text = _client().get("/new-tab").text
    assert "octo" in text and "wright" in text
    assert "Octowright" not in text


def test_new_tab_references_otto_svg_locally() -> None:
    assert "/otto.svg" in _client().get("/new-tab").text


def test_new_tab_has_no_external_requests() -> None:
    text = _client().get("/new-tab").text
    for external in ("https://", "http://fonts.", "cdn.", "googleapis"):
        assert external not in text, f"found external reference: {external}"


def test_otto_svg_returns_200() -> None:
    assert _client().get("/otto.svg").status_code == 200


def test_otto_svg_content_type() -> None:
    assert "svg" in _client().get("/otto.svg").headers["content-type"]
