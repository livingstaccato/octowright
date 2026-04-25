# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Unit tests for the `octowright_dashboard_url` MCP tool."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octowright import http_server as _http
from octowright.server import _state
from octowright.server.meta import octowright_dashboard_url


@pytest.fixture(autouse=True)
def reset_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Each test starts with a clean runtime state and an empty live pool.

    Recordings dir is redirected to a tmp path so the closed-session count
    is deterministic.
    """
    monkeypatch.setattr(_http, "_RUNTIME_HOST", None)
    monkeypatch.setattr(_http, "_RUNTIME_PORT", None)
    monkeypatch.setattr(_http, "_RUNTIME_ERROR", None)

    rec = tmp_path / "rec"
    rec.mkdir()
    # The tool reads RECORDINGS_DIR from defaults at call time (re-imported).
    from octowright import defaults as _defaults

    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)

    fake_pool = SimpleNamespace(_sessions={})
    fake_spool = SimpleNamespace(list_live=lambda: [])
    monkeypatch.setattr(_state, "pool", fake_pool)
    monkeypatch.setattr(_state, "scenario_pool", fake_spool)
    # The meta module captured `pool` and `scenario_pool` at import; rebind there too.
    from octowright.server import meta as _meta

    monkeypatch.setattr(_meta, "pool", fake_pool)
    monkeypatch.setattr(_meta, "scenario_pool", fake_spool)
    return rec


def test_running_is_false_when_http_not_started() -> None:
    result = octowright_dashboard_url()
    assert result["running"] is False
    assert result["url"] is None
    assert result["session_url"] is None
    assert "error" in result


def test_running_is_true_with_runtime_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "_RUNTIME_HOST", "127.0.0.1")
    monkeypatch.setattr(_http, "_RUNTIME_PORT", 8765)
    result = octowright_dashboard_url()
    assert result["running"] is True
    assert result["url"] == "http://127.0.0.1:8765/"
    assert result["session_url"] is None
    assert "error" not in result


def test_session_deep_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "_RUNTIME_HOST", "127.0.0.1")
    monkeypatch.setattr(_http, "_RUNTIME_PORT", 8765)
    result = octowright_dashboard_url(session_id="abc123")
    assert result["session_url"] == "http://127.0.0.1:8765/sessions/abc123"


def test_counts_reflect_pool_and_recordings(
    reset_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two on-disk recordings = two closed sessions.
    (reset_runtime / "20260101T000000Z-chromium-aaa.jsonl").write_text("")
    (reset_runtime / "20260101T000000Z-firefox-bbb.jsonl").write_text("")
    # One live session.
    fake_session = SimpleNamespace(instance_id="live01")
    from octowright.server import meta as _meta

    _meta.pool._sessions["live01"] = fake_session

    monkeypatch.setattr(_http, "_RUNTIME_HOST", "127.0.0.1")
    monkeypatch.setattr(_http, "_RUNTIME_PORT", 8765)
    result = octowright_dashboard_url()
    assert result["live_sessions"] == 1
    assert result["closed_sessions"] == 2
    assert result["live_scenarios"] == 0


def test_error_propagates_from_runtime_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "_RUNTIME_HOST", None)
    monkeypatch.setattr(_http, "_RUNTIME_PORT", None)
    monkeypatch.setattr(_http, "_RUNTIME_ERROR", "port 8765 is in use")
    result = octowright_dashboard_url()
    assert result["running"] is False
    assert result["error"] == "port 8765 is in use"
