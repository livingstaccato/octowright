#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live follower<->leader bridge check — today, and across release skew.

Question 1 (default): a just-started stdio follower of THIS checkout bridges
to the running daemon and serves real calls. Exits 0 with byte counts; a
failure means the client handle is fine and the daemon is at fault (per
docs/troubleshooting.md), because the followers and the daemon here are one
version.

Question 2 (``--follower <version|previous>``): the follower is installed
from a DIFFERENT PyPI release (``uvx --from octowright==<version>``) while
the leader is this checkout — the exact wiring an upgrade produces, since a
follower is only ever redeployed by its own client reconnecting. The leader
is spawned by this script into a throwaway state root (own lockfile, bridge
state, recordings), so the host's real daemon and lockfile are never touched;
``--keep`` preserves that root for debugging. A skew pairing that cannot even
complete the MCP handshake is a release-boundary break this catches before
the client population does.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_dead_leader_smoke import free_tcp_port, smoke_env, terminate_pid

ROOT = Path(__file__).resolve().parents[1]


def previous_release() -> str:
    """The release tag before the newest one (v-stripped), from git."""
    tags = subprocess.check_output(  # nosec B603
        ["git", "tag", "--sort=-version:refname"], cwd=ROOT, text=True
    ).split()
    versions = [tag.lstrip("v") for tag in tags if re.fullmatch(r"v?\d+\.\d+\.\d+", tag)]
    if len(versions) < 2:
        raise RuntimeError("cannot resolve 'previous': fewer than two release tags; pass --follower <version>")
    return versions[1]


async def _call_status(session: ClientSession) -> str:
    result = await session.call_tool("octowright_status", {})
    return result.content[0].text


async def roundtrip(params: StdioServerParameters, *, timeout: float) -> tuple[int, list[str]]:
    """One stdio MCP client: initialize -> tools/list -> two tool calls."""
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        with anyio.fail_after(timeout):
            await session.initialize()
            tools = await session.list_tools()
            first = await _call_status(session)
            second = await _call_status(session)
    return len(tools.tools), [first, second]


def report(tools: int, statuses: list[str]) -> None:
    print(f"tools={tools}")
    print(f"first_status_bytes={len(statuses[0])}")
    print(f"second_status_bytes={len(statuses[1])}")


def wait_for_health_up(port: int, *, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.0) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"isolated leader on :{port} never answered /api/health")


def leader_version(port: int) -> str:
    with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2.0) as response:
        body = json.loads(response.read().decode("utf-8"))
    return str(body.get("version") or "unknown")


def follower_versions(bridge_state_path: Path) -> list[str]:
    """The versions each skew-mode follower reported in its bridge snapshot."""
    try:
        state = json.loads(bridge_state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    followers = state.get("followers")
    if not isinstance(followers, dict):
        return []
    found: list[str] = []
    for snap in followers.values():
        if isinstance(snap, dict):
            version = snap.get("follower_version")
            found.append(str(version) if isinstance(version, str) and version else "unknown")
    return sorted(set(found))


async def run_ambient() -> int:
    params = StdioServerParameters(command=".venv/bin/octowright", args=["serve"], cwd=".")
    tools, statuses = await roundtrip(params, timeout=30)
    report(tools, statuses)
    return 0


async def run_skew(version: str, *, keep: bool) -> int:
    tmp = tempfile.mkdtemp(prefix="octowright-bridge-skew-")
    root = Path(tmp)
    port = free_tcp_port()
    env = smoke_env(root, port)
    leader_script = ROOT / ".venv" / "bin" / "octowright"
    if not leader_script.exists():
        raise RuntimeError(f"leader needs this checkout's install at {leader_script} (run `make install`)")
    log_path = root / "leader.log"
    with open(log_path, "w", encoding="utf-8") as log_file:
        leader = subprocess.Popen(  # nosec B603
            [
                str(leader_script),
                "serve",
                "--daemon-mode",
                "--http-host",
                "127.0.0.1",
                "--http-port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_health_up(port)
            print(f"leader={leader_version(port)} (isolated, :{port})")
            params = StdioServerParameters(
                command="uvx",
                args=["--from", f"octowright=={version}", "octowright", "serve"],
                cwd=ROOT,
                env=env,
            )
            # uvx may have to download the follower release on a cold cache.
            tools, statuses = await roundtrip(params, timeout=180)
            report(tools, statuses)
            versions = follower_versions(root / "state" / "bridge-state.json")
            print(f"pairing: leader={leader_version(port)} follower={versions or ['unreported']}")
            if versions == ["unknown"]:
                print("(that release predates follower-version reporting; the bridge still answered calls)")
        finally:
            terminate_pid(leader.pid or -1)
    if keep:
        print(f"kept isolated state at {root}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--follower",
        default="current",
        metavar="current|previous|X.Y.Z",
        help="Which release the stdio follower comes from. 'previous' resolves from git tags; "
        "anything other than 'current' runs against an isolated leader spawned from this checkout.",
    )
    parser.add_argument("--keep", action="store_true", help="Keep the isolated state root (skew mode).")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    if args.follower == "current":
        return await run_ambient()
    version = previous_release() if args.follower == "previous" else args.follower
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError(f"--follower must be 'current', 'previous', or an X.Y.Z release, got {args.follower!r}")
    print(f"follower=octowright=={version} (uvx)")
    return await run_skew(version, keep=args.keep)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return anyio.run(run, args)
    except Exception as exc:
        print(f"SMOKE FAIL: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
