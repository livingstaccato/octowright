# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright dashboard`` — pairing-ticket mint CLI."""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import click
import pytest
from click.testing import CliRunner

from octowright.cli import dashboard as dash_mod
from octowright.singleton import LeaderInfo


def _leader(token: str = "cap-token") -> LeaderInfo:
    return LeaderInfo(
        pid=4242,
        http_host="127.0.0.1",
        http_port=6286,
        mcp_url="http://127.0.0.1:6286/mcp/",
        started_at=0.0,
        token=token,
    )


def test_dashboard_prints_pair_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octowright.singleton.read_lock", lambda *a, **k: _leader())
    monkeypatch.setattr(dash_mod, "_mint_ticket", lambda base, token: "TICKET123")
    result = CliRunner().invoke(dash_mod.dashboard, [])
    assert result.exit_code == 0
    assert "http://127.0.0.1:6286/pair#TICKET123" in result.output


def test_dashboard_no_daemon_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octowright.singleton.read_lock", lambda *a, **k: None)
    result = CliRunner().invoke(dash_mod.dashboard, [])
    assert result.exit_code != 0
    assert "no running octowright daemon" in result.output


def test_dashboard_tokenless_leader_falls_back_to_plain_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octowright.singleton.read_lock", lambda *a, **k: _leader(token=""))
    result = CliRunner().invoke(dash_mod.dashboard, [])
    assert result.exit_code == 0
    assert "pairing is unavailable" in result.output
    assert "http://127.0.0.1:6286/" in result.output


def test_dashboard_open_uses_redirect_file_not_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octowright.singleton.read_lock", lambda *a, **k: _leader())
    monkeypatch.setattr(dash_mod, "_mint_ticket", lambda base, token: "TICKET123")
    opened: list[str] = []
    monkeypatch.setattr(dash_mod.webbrowser, "open", lambda target: opened.append(target) or True)
    result = CliRunner().invoke(dash_mod.dashboard, ["--open"])
    assert result.exit_code == 0
    assert len(opened) == 1
    # The browser gets a file:// path, never the ticket-bearing URL in argv.
    assert opened[0].startswith("file://")
    assert "TICKET123" not in opened[0]


def test_mint_ticket_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def read(self) -> bytes:
            return json.dumps({"ok": True, "ticket": "T"}).encode()

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float = 0) -> _Resp:
        captured["url"] = request.full_url
        captured["token"] = request.get_header("X-octowright-token")
        return _Resp()

    monkeypatch.setattr(dash_mod.urllib.request, "urlopen", fake_urlopen)
    assert dash_mod._mint_ticket("http://127.0.0.1:6286", "cap") == "T"
    assert captured["url"] == "http://127.0.0.1:6286/api/pair/mint"
    assert captured["token"] == "cap"


def test_mint_ticket_http_error_is_click_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: float = 0) -> Any:
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", None, io.BytesIO(b"denied"))  # type: ignore[arg-type]

    monkeypatch.setattr(dash_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(click.ClickException, match="403"):
        dash_mod._mint_ticket("http://127.0.0.1:6286", "cap")
