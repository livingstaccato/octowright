# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright_terminal import tools
from octowright_terminal.connector_config import TELNET_DEFAULT_PORT


def test_telnet_connector_config_maps_host_and_port() -> None:
    cfg = tools._telnet_connector_config(host="bbs.example.com", port=23)
    assert cfg["host"] == "bbs.example.com"
    assert cfg["port"] == 23
    # Telnet connector must not carry PTY-only, SSH-only, or unrecognised keys.
    assert "command" not in cfg
    assert "cols" not in cfg
    assert "rows" not in cfg
    assert "username" not in cfg
    assert "known_hosts" not in cfg
    assert "input_mode" not in cfg


def test_telnet_connector_config_only_emits_valid_connector_keys() -> None:
    from provide.uterm.server.connectors.telnet import TelnetSessionConnector

    cfg = tools._telnet_connector_config(host="h", port=23)
    assert set(cfg) <= TelnetSessionConnector._VALID_CONFIG_KEYS
    assert cfg.get("hub_overlay") is False


def test_telnet_default_port_is_23() -> None:
    assert TELNET_DEFAULT_PORT == 23


async def test_telnet_launch_defaults_to_port_23() -> None:
    # terminal_launch with kind="telnet" and no port must apply the default (23).
    # The connector raises on connect (no server), so we catch the pool error;
    # the config dict is built synchronously before the connect call.
    from unittest.mock import patch

    captured: dict = {}

    async def fake_launch(*, kind: str, connector_config: dict, **_kw: object) -> dict:
        captured.update(connector_config=connector_config)
        return {"instance_id": "x", "kind": kind}

    with patch.object(tools._pool(), "launch", side_effect=fake_launch):
        await tools.terminal_launch(kind="telnet", host="h")

    assert captured["connector_config"]["port"] == 23
    assert captured["connector_config"]["host"] == "h"
