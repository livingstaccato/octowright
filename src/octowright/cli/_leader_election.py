# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader-election probes for ``cli/serve``.

Lockfile+HTTP liveness (``_probe_alive_leader``), the canonical-port split-brain
guard (``_canonical_port_serves_octowright``), and adopting an already-live leader
instead of forking a competitor (``_adopt_canonical_leader``). Split out of
``cli/serve`` to keep that module under its LOC ceiling; ``serve`` calls these as
``_election.X`` and tests monkeypatch them there.
"""

from __future__ import annotations

from typing import Any

# When the canonical port already serves octowright but the lockfile probe missed
# it (the lockfile lags the port bind by a few ms), wait this long for the lock to
# appear so we can ADOPT the existing leader instead of spawning a competitor.
_CANONICAL_LEADER_WAIT_ATTEMPTS = 10
_CANONICAL_LEADER_WAIT_INTERVAL = 0.2


async def _probe_alive_leader(sn: Any) -> Any | None:
    """Return LeaderInfo iff lockfile + HTTP probe both confirm live AND the
    recorded leader is loopback.

    The host check belongs here, before anything DIALS the recorded URL. The
    0600 lockfile is writable by any same-user process, and this info flows two
    ways: into ``serve._run_follower`` (the MCP bridge, carrying tool arguments
    with persona credentials substituted in) and into the ``/api/health`` probe
    on the line below. Validating only inside the bridge still lets the health
    GET reach an attacker-chosen host, and lets a 200 from that host make a
    poisoned lock look live.

    Rejecting returns ``None`` -- "no live leader" -- so ``serve`` goes on to
    elect a real one rather than being wedged by the poisoned lock.
    """
    info = sn.read_lock()
    if info is None or sn.is_stale(info):
        return None
    # Lazy import: keeps `octowright.cli` free of the heavy stack the follower
    # never needs (see tests/test_follower_import_weight.py).
    from octowright.proxy_runtime import _leader_url_is_safe

    if not _leader_url_is_safe(info.mcp_url):
        return None
    return info if await sn.probe_http_alive(info) else None


async def _canonical_port_serves_octowright(http_host: str | None, http_port: int | None) -> bool:
    """True iff the PREFERRED HTTP port already answers ``/api/health`` as octowright.

    Split-brain guard, independent of the lockfile. ``_probe_alive_leader`` trusts
    the lockfile, which can false-negative during a storm — a healthy leader that's
    momentarily slow, or a lockfile a racing respawn already repointed. Spawning
    then makes ``http/lifespan`` walk the busy canonical port up to a BUMPED one
    (e.g. 6286 → 6287) and bind a SECOND leader beside the healthy one (observed
    live). So before spawning, confirm the canonical port isn't already octowright.
    """
    import httpx

    from octowright.defaults import HTTP_HOST, HTTP_PORT
    from octowright.http.exposure import is_loopback_host

    host = http_host or HTTP_HOST
    # A wildcard/non-loopback bind still answers on loopback; probe there.
    probe_host = host if is_loopback_host(host) else "127.0.0.1"
    port = http_port if http_port is not None else HTTP_PORT
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"http://{probe_host}:{port}/api/health")
        if response.status_code != 200:
            return False
        body = response.json()
        return isinstance(body, dict) and body.get("ok") is True
    except (httpx.HTTPError, OSError, ValueError):
        return False


async def _adopt_canonical_leader(sn: Any, http_host: str | None, http_port: int | None) -> Any | None:
    """If a live octowright already holds the canonical HTTP port but the lockfile
    probe missed it, briefly re-probe the lockfile and return that leader so we
    ATTACH to it instead of spawning a second leader on a bumped port. Returns None
    when the canonical port is free / not octowright (spawning is legitimate), or —
    rarely — when it serves octowright but never publishes a readable lockfile (we
    need its mcp_url + token from the lock to follow it, so we fall through)."""
    import asyncio

    if not await _canonical_port_serves_octowright(http_host, http_port):
        return None
    for _ in range(_CANONICAL_LEADER_WAIT_ATTEMPTS):
        if (found := await _probe_alive_leader(sn)) is not None:
            return found
        await asyncio.sleep(_CANONICAL_LEADER_WAIT_INTERVAL)
    return None
