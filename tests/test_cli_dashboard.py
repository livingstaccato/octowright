# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright dashboard`` — pairing-code mint CLI."""

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


def _leader(token: str = "cap-token", host: str = "127.0.0.1", port: int = 6286) -> LeaderInfo:
    return LeaderInfo(
        pid=4242,
        http_host=host,
        http_port=port,
        mcp_url=f"http://{host}:{port}/mcp/",
        started_at=0.0,
        token=token,
    )


def test_dashboard_prints_pair_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octowright.singleton.read_lock", lambda *a, **k: _leader())
    monkeypatch.setattr(dash_mod, "_mint_code", lambda base, token: "CODE123")
    result = CliRunner().invoke(dash_mod.dashboard, [])
    assert result.exit_code == 0
    assert "http://127.0.0.1:6286/pair#CODE123" in result.output
    assert "cap-token" not in result.output


def test_dashboard_uses_bracketed_ipv6_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octowright.singleton.read_lock", lambda *a, **k: _leader(host="::1"))
    monkeypatch.setattr(dash_mod, "_mint_code", lambda base, token: "CODE123")
    result = CliRunner().invoke(dash_mod.dashboard, [])
    assert result.exit_code == 0
    assert "http://[::1]:6286/pair#CODE123" in result.output


@pytest.mark.parametrize("host", ["127.0.0.1/path", "localhost?x=1", "user@localhost", "localhost\n.example"])
def test_dashboard_rejects_invalid_lockfile_host(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setattr("octowright.singleton.read_lock", lambda *a, **k: _leader(host=host))
    result = CliRunner().invoke(dash_mod.dashboard, [])
    assert result.exit_code != 0
    assert "invalid dashboard host" in result.output


def test_dashboard_rejects_remote_host_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    monkeypatch.setattr("octowright.singleton.read_lock", lambda *a, **k: _leader(host="dashboard.example"))
    result = CliRunner().invoke(dash_mod.dashboard, [])
    assert result.exit_code != 0
    assert "remote dashboard access is disabled" in result.output


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
    monkeypatch.setattr(dash_mod, "_mint_code", lambda base, token: "CODE123")
    opened: list[str] = []
    monkeypatch.setattr(dash_mod.webbrowser, "open", lambda target: opened.append(target) or True)
    result = CliRunner().invoke(dash_mod.dashboard, ["--open"])
    assert result.exit_code == 0
    assert len(opened) == 1
    # The browser gets a file:// path, never the code-bearing URL in argv.
    assert opened[0].startswith("file://")
    assert "CODE123" not in opened[0]


def test_mint_code_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def read(self) -> bytes:
            return json.dumps({"code": "C", "expires_in": 60}).encode()

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
    assert dash_mod._mint_code("http://127.0.0.1:6286", "cap") == "C"
    assert captured["url"] == "http://127.0.0.1:6286/api/pair/mint"
    assert captured["token"] == "cap"


def test_mint_code_http_error_is_click_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: float = 0) -> Any:
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", None, io.BytesIO(b"denied"))  # type: ignore[arg-type]

    monkeypatch.setattr(dash_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(click.ClickException, match="403"):
        dash_mod._mint_code("http://127.0.0.1:6286", "cap")


def test_mint_code_unreachable_is_click_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: float = 0) -> Any:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(dash_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(click.ClickException, match="could not reach the leader"):
        dash_mod._mint_code("http://127.0.0.1:6286", "cap")
