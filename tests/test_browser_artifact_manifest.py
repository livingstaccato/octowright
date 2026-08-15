# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the browser_artifact_manifest MCP tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from octowright.server.browser import artifact_manifest as _artifact_manifest


@pytest.fixture(autouse=True)
def _patch_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_pool = MagicMock()
    monkeypatch.setattr(_artifact_manifest, "pool", fake_pool)
    return fake_pool


def test_returns_manifest_with_live_flag_for_live_session(
    monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock
) -> None:
    _patch_pool.maybe_get.return_value = object()
    manifest = {
        "log_path": "/rec/x.jsonl",
        "video_path": "/rec/videos/x/x.webm",
        "trace_path": None,
        "har_path": None,
    }
    monkeypatch.setattr("octowright.http.discovery.resolve_session_artifacts", lambda _sid: manifest)

    result = _artifact_manifest.browser_artifact_manifest("live1")

    assert result == {"instance_id": "live1", "live": True, **manifest}


def test_returns_manifest_with_live_false_for_closed_session(
    monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock
) -> None:
    _patch_pool.maybe_get.return_value = None
    manifest = {
        "log_path": "/rec/y.jsonl",
        "video_path": None,
        "trace_path": None,
        "har_path": None,
    }
    monkeypatch.setattr("octowright.http.discovery.resolve_session_artifacts", lambda _sid: manifest)

    result = _artifact_manifest.browser_artifact_manifest("closed1")

    assert result == {"instance_id": "closed1", "live": False, **manifest}


def test_returns_error_when_nothing_found(monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock) -> None:
    _patch_pool.maybe_get.return_value = None
    monkeypatch.setattr(
        "octowright.http.discovery.resolve_session_artifacts",
        lambda _sid: {"log_path": None, "video_path": None, "trace_path": None, "har_path": None},
    )

    result = _artifact_manifest.browser_artifact_manifest("nope123")

    assert result == {
        "instance_id": "nope123",
        "error": "no live session or recording found for instance_id 'nope123'",
    }
