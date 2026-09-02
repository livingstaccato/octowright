# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from pathlib import Path

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


async def test_ssh_key_path_authenticates_against_a_real_server(tmp_path: Path) -> None:
    """The construction guard above proves ``client_key`` survives the connector's
    gates; it does not prove asyncssh actually accepts a scalar string as a key
    and authenticates with it. Adversarial review (codex/assumptions, c-0003)
    flagged exactly that gap: the unit tests only inspect ``_client_keys``,
    so a future asyncssh/uterm version that stopped treating a bare string as a
    file path would still pass every test here while every real keyed SSH
    launch failed. This spins up a real in-process SSH server -- no system
    sshd, no ``authorized_keys`` mutation -- and drives the actual
    ``SshSessionConnector`` through ``ssh_connector_config``'s emitted
    ``client_key`` end to end: connect, authenticate, exchange data, close.
    """
    import asyncssh
    from provide.uterm.server.connectors.ssh import SshSessionConnector

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key_path = tmp_path / "client_key"
    client_key_path.write_bytes(client_key.export_private_key())
    client_key_path.chmod(0o600)
    authorized_keys = asyncssh.import_authorized_keys(client_key.export_public_key().decode("ascii"))

    async def _handle_process(process: asyncssh.SSHServerProcess) -> None:
        process.stdout.write("server-ready\n")
        async for line in process.stdin:
            process.stdout.write(f"echo:{line}")
            if line.strip() == "exit":
                break
        process.exit(0)

    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=authorized_keys,
        process_factory=_handle_process,
    )
    try:
        port = server.sockets[0].getsockname()[1]
        cfg = tools._ssh_connector_config(
            host="127.0.0.1",
            port=port,
            user="whoever",
            key_path=str(client_key_path),
            password=None,
            known_hosts=None,
            insecure_no_host_check=True,
        )
        connector = SshSessionConnector("sess-live", "ssh", cfg)
        await connector.start()
        try:
            text = ""
            for _ in range(50):
                for msg in await connector.poll_messages():
                    if msg["type"] == "term":
                        text += msg["data"]
                if "server-ready" in text:
                    break
                await asyncio.sleep(0.05)
            assert "server-ready" in text, text

            await connector.handle_input("ping\n")
            for _ in range(50):
                for msg in await connector.poll_messages():
                    if msg["type"] == "term":
                        text += msg["data"]
                if "echo:ping" in text:
                    break
                await asyncio.sleep(0.05)
            assert "echo:ping" in text, text
        finally:
            await connector.stop()
    finally:
        server.close()
        await server.wait_closed()
