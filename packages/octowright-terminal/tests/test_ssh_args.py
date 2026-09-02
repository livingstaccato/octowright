# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright_terminal import tools


def test_ssh_connector_config_maps_to_uterm_keys() -> None:
    cfg = tools._ssh_connector_config(
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
    assert cfg["client_key"] == "/k"
    assert cfg["known_hosts"] == "/kh"
    # SSH config must not carry PTY-only keys: the uterm SSH connector rejects
    # unknown keys, and it fixes the remote PTY size itself (no cols/rows).
    assert "command" not in cfg
    assert "cols" not in cfg
    assert "rows" not in cfg


def test_ssh_connector_config_is_accepted_by_the_connector() -> None:
    """Contract guard: the connector must ACCEPT what we emit, not merely allow-list it.

    Membership in ``_VALID_CONFIG_KEYS`` is too weak a check to catch the bug it
    was written for. ``client_key_path`` is *in* that allow-list and the
    connector then raises on it by name two lines later, so this test passed
    while every keyed SSH launch failed with "client_key_path is not
    supported". Constructing the connector exercises both gates at once.
    """
    from provide.uterm.server.connectors.ssh import SshSessionConnector

    cfg = tools._ssh_connector_config(
        host="h",
        port=22,
        user="me",
        key_path="/k",
        password="pw",  # pragma: allowlist secret
        known_hosts="/kh",
        insecure_no_host_check=True,
    )
    assert set(cfg) <= SshSessionConnector._VALID_CONFIG_KEYS
    # Construction is the real assertion: it runs the unknown-key check AND
    # the per-key rejections. A key path must survive both.
    connector = SshSessionConnector("sess-1", "ssh", cfg)
    assert connector._client_keys == ["/k"]


async def test_ssh_key_path_passes_the_egress_chokepoint() -> None:
    """The second gate rejects ``client_key_path`` independently of the connector.

    Both must accept the emitted config: a launch is refused if either one
    raises, and they are separate code paths in uterm.
    """
    from provide.uterm.server.egress import assert_session_egress_allowed

    cfg = tools._ssh_connector_config(
        host="127.0.0.1",
        port=22,
        user="me",
        key_path="/k",
        password=None,
        known_hosts="/kh",
        insecure_no_host_check=False,
    )
    await assert_session_egress_allowed("ssh", cfg, block_private=False)


async def test_ssh_launch_without_known_hosts_returns_clean_error() -> None:
    # The connector raises ValueError synchronously in build_connector when
    # known_hosts is absent and host-key checking isn't explicitly disabled;
    # terminal_launch must convert that into a clean tool-error dict.
    result = await tools.terminal_launch(kind="ssh", host="h", user="me")
    assert result.get("ok") is False
    assert "known_hosts" in result["error"]
