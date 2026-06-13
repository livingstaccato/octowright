# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.server.terminal import lifecycle


def test_ssh_connector_config_maps_to_uterm_keys() -> None:
    cfg = lifecycle._ssh_connector_config(
        host="h",
        port=2222,
        user="me",
        key_path="/k",
        password=None,
        known_hosts="/kh",
        insecure_no_host_check=False,
    )
    assert cfg["host"] == "h"
    assert cfg["port"] == 2222
    assert cfg["username"] == "me"
    assert cfg["client_key_path"] == "/k"
    assert cfg["known_hosts"] == "/kh"
    # SSH config must not carry PTY-only keys: the uterm SSH connector rejects
    # unknown keys, and it fixes the remote PTY size itself (no cols/rows).
    assert "command" not in cfg
    assert "cols" not in cfg
    assert "rows" not in cfg


def test_ssh_connector_config_only_emits_valid_uterm_keys() -> None:
    # Contract guard: every key we emit must be in the connector's allow-list,
    # or build_connector() raises "unknown ssh connector_config keys".
    from provide.uterm.server.connectors.ssh import SshSessionConnector

    cfg = lifecycle._ssh_connector_config(
        host="h",
        port=22,
        user="me",
        key_path="/k",
        password="pw",  # pragma: allowlist secret
        known_hosts="/kh",
        insecure_no_host_check=True,
    )
    assert set(cfg) <= SshSessionConnector._VALID_CONFIG_KEYS


async def test_ssh_launch_without_known_hosts_returns_clean_error() -> None:
    # The connector raises ValueError synchronously in build_connector when
    # known_hosts is absent and host-key checking isn't explicitly disabled;
    # terminal_launch must convert that into a clean tool-error dict.
    result = await lifecycle.terminal_launch(kind="ssh", host="h", user="me")
    assert result.get("ok") is False
    assert "known_hosts" in result["error"]
