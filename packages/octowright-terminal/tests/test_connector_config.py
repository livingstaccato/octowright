# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright_terminal.connector_config import pty_connector_config, ssh_connector_config, telnet_connector_config


def test_pty_config_defaults_and_overrides() -> None:
    assert pty_connector_config(command=None, cols=None, rows=None) == {
        "command": "/bin/bash",
        "cols": 80,
        "rows": 24,
    }
    assert pty_connector_config(command="/bin/zsh", cols=132, rows=50) == {
        "command": "/bin/zsh",
        "cols": 132,
        "rows": 50,
    }


def test_ssh_config_emits_only_connector_keys() -> None:
    cfg = ssh_connector_config(
        host="h",
        port=2222,
        user="me",
        key_path="/k",
        password=None,
        known_hosts="/kh",
        insecure_no_host_check=False,
    )
    assert cfg == {
        "port": 2222,
        "host": "h",
        "username": "me",
        "client_key": "/k",
        "known_hosts": "/kh",
    }
    assert "cols" not in cfg and "rows" not in cfg and "command" not in cfg


def test_telnet_config_host_and_port() -> None:
    cfg = telnet_connector_config(host="bbs.example.com", port=23)
    assert cfg == {"host": "bbs.example.com", "port": 23, "hub_overlay": False}
    # No PTY-only or SSH-only keys.
    assert "command" not in cfg and "cols" not in cfg and "rows" not in cfg
    assert "username" not in cfg and "known_hosts" not in cfg


def test_telnet_config_omits_host_when_none() -> None:
    cfg = telnet_connector_config(host=None, port=9999)
    assert cfg == {"port": 9999, "hub_overlay": False}
    assert "host" not in cfg


def test_telnet_config_only_emits_valid_connector_keys() -> None:
    from provide.uterm.server.connectors.telnet import TelnetSessionConnector

    cfg = telnet_connector_config(host="h", port=23)
    assert set(cfg) <= TelnetSessionConnector._VALID_CONFIG_KEYS


def test_ssh_config_insecure_flag_and_password() -> None:
    cfg = ssh_connector_config(
        host="h",
        port=22,
        user=None,
        key_path=None,
        password="pw",  # pragma: allowlist secret
        known_hosts=None,
        insecure_no_host_check=True,
    )
    assert cfg["password"] == "pw"  # pragma: allowlist secret
    assert cfg["insecure_no_host_check"] is True
    assert "known_hosts" not in cfg  # omitted args dropped


import pytest  # noqa: E402


@pytest.mark.parametrize("raw", ["22s", "", "  ", "0", "70000", "-1", "2.5"])
def test_an_unusable_ssh_port_env_falls_back_to_22(raw: str) -> None:
    """A bare ``int()`` at import turned one typo in OCTOWRIGHT_SSH_PORT into
    an ImportError for the whole plugin -- and with it every terminal tool."""
    from octowright_terminal.connector_config import _ssh_default_port

    assert _ssh_default_port({"OCTOWRIGHT_SSH_PORT": raw}) == 22


@pytest.mark.parametrize(("raw", "expected"), [(" 2222 ", 2222), ("65535", 65535), ("1", 1)])
def test_a_valid_ssh_port_env_is_used(raw: str, expected: int) -> None:
    from octowright_terminal.connector_config import _ssh_default_port

    assert _ssh_default_port({"OCTOWRIGHT_SSH_PORT": raw}) == expected


def test_the_module_imports_with_a_bad_ssh_port(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    import octowright_terminal.connector_config as cc

    monkeypatch.setenv("OCTOWRIGHT_SSH_PORT", "twenty-two")
    try:
        assert importlib.reload(cc).SSH_DEFAULT_PORT == 22
    finally:
        monkeypatch.delenv("OCTOWRIGHT_SSH_PORT")
        importlib.reload(cc)
