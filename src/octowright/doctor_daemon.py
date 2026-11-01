# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright doctor``'s checks of the running deployment, not the machine.

Three questions about whatever daemon the lockfile names, each one a loopback
probe and none of them launching a browser: is the recorded leader really
alive (``check_daemon``), is a SECOND, uncoordinated leader answering beside
it (``check_canonical_port``), and do the live followers run the same code as
that leader (``check_followers``). ``doctor.run_checks`` calls them; the
machine-level checks and the engine probes stay in ``doctor``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from octowright.doctor import Check, Status
from octowright.mcp_identity import RECONNECT_STEPS, ClientReconnect, client_step
from octowright.singleton import pid_is_alive, read_lock

_UNKNOWN_CLIENT_STEP = (
    "the owning MCP client could not be identified from its process tree — reconnect whichever client "
    "spawned it; as a last resort `octowright restart --kill-followers` cuts every follower at once"
)


async def check_daemon() -> Check:
    """Whether a leader is recorded, and whether that record is still true.

    A live pid is not enough: the OS recycles a dead daemon's pid, so the
    recorded leader must also answer ``/api/health``.
    """
    from octowright import singleton

    info = singleton.read_lock()
    if info is None:
        return Check("daemon", "ok", "no daemon running (no lockfile)", {"running": False})
    if not singleton.pid_is_alive(info.pid):
        return Check(
            "daemon",
            "warn",
            f"lockfile names pid {info.pid}, which is not a live daemon — stale lock",
            {"running": False, "pid": info.pid, "stale": True},
        )
    if not await singleton.probe_http_alive(info):
        return Check(
            "daemon",
            "warn",
            f"lockfile names live pid {info.pid}, but nothing answers at {info.http_host}:{info.http_port} "
            "— stale lock (recycled pid) or a wedged daemon",
            {"running": False, "pid": info.pid, "stale": True},
        )
    return Check(
        "daemon",
        "ok",
        f"leader pid {info.pid} at {info.http_host}:{info.http_port}",
        {"running": True, "pid": info.pid, "url": f"http://{info.http_host}:{info.http_port}"},
    )


async def _live_octowright_ports(host: str, ports: list[int]) -> list[int]:
    """Which of ``ports`` currently answer ``/api/health`` as octowright."""
    from octowright.cli._leader_election import _canonical_port_serves_octowright

    answers = await asyncio.gather(*(_canonical_port_serves_octowright(host, port) for port in ports))
    return [port for port, answered in zip(ports, answers, strict=True) if answered]


async def check_canonical_port() -> Check:
    """Whether a SECOND, uncoordinated leader is alive beside the recorded one.

    ``check_daemon`` only reports on the lockfile's recorded leader. A daemon
    started outside octowright's own election path -- e.g. a systemd unit
    whose ``ExecStart`` runs ``serve --daemon-mode`` directly, which by design
    skips the lock/election check -- can bind a port at the same moment a
    CLI-triggered spawn wins the race and lands elsewhere. Both stay alive; the
    lockfile records only one of them, so ``check_daemon`` alone reports a
    clean single leader while a second one answers unrecorded.

    Which one the lockfile ends up recording is a RACE, not a property:
    ``cli/serve._on_http_bound`` writes the lock with whatever port it actually
    bound, after the port walk, for every non-``--no-singleton`` leader. So the
    second daemon can be on the canonical port OR on a bumped one depending
    only on bind order, and probing just the canonical port would return a
    clean ``ok`` for half of the cases this check exists to catch. Every port
    the leader is NOT on that a second daemon could hold is probed.
    """
    from octowright import defaults

    info = read_lock()
    if info is None or not pid_is_alive(info.pid):
        return Check("daemon:canonical-port", "skip", "no live recorded leader -- nothing to cross-check", {})

    canonical_host, canonical_port = defaults.HTTP_HOST, defaults.HTTP_PORT
    # `http/lifespan` walks CONTIGUOUSLY upward from the canonical port, at most
    # HTTP_PORT_RETRIES times, so this is the complete set of ports a leader of
    # this deployment can be on.
    bumped = list(range(canonical_port + 1, canonical_port + defaults.HTTP_PORT_RETRIES + 1))
    expected = [canonical_port, *bumped]
    data: dict[str, Any] = {"canonical_port": canonical_port, "leader_port": info.http_port}

    if info.http_port not in expected:
        # `defaults.HTTP_PORT` is read from THIS process's environment at import
        # time, while the recorded port came from the daemon's. A leader outside
        # the walk range is therefore far more likely to be deliberately
        # configured than split-brained -- and the deployment this check targets
        # (systemd/launchd, with `Environment=OCTOWRIGHT_HTTP_PORT=...`) is
        # exactly the one whose environment an operator's shell does not share.
        # Reporting a split-brain here would be a false FAIL, and doctor exits 1
        # on any FAIL.
        return Check(
            "daemon:canonical-port",
            "warn",
            f"leader is on {info.http_host}:{info.http_port}, which is neither the canonical port this "
            f"doctor resolved ({canonical_port}) nor one the port walk can reach ({bumped[0]}-{bumped[-1]}) "
            "-- most likely the daemon was started with a different OCTOWRIGHT_HTTP_PORT than this shell "
            "has, so there is nothing meaningful to cross-check; set the same value here to compare",
            data,
        )

    intruders = await _live_octowright_ports(canonical_host, [port for port in expected if port != info.http_port])
    data["second_daemon_ports"] = intruders
    if intruders:
        ports = ", ".join(str(port) for port in intruders)
        return Check(
            "daemon:canonical-port",
            "fail",
            f"two live daemons: the recorded leader on {info.http_host}:{info.http_port}, and a SECOND, "
            f"uncoordinated one answering on {canonical_host}:{ports} -- likely a systemd/launchd-managed "
            "daemon started outside octowright's election lock; run `octowright restart --keep-browsers` "
            "to reclaim the canonical port",
            data,
        )

    if info.http_port == canonical_port:
        return Check(
            "daemon:canonical-port",
            "ok",
            f"leader holds the canonical port ({canonical_host}:{canonical_port}) and nothing else answers "
            f"on {bumped[0]}-{bumped[-1]}",
            data,
        )
    return Check(
        "daemon:canonical-port",
        "warn",
        f"leader is on {info.http_host}:{info.http_port}, not the canonical port "
        f"{canonical_host}:{canonical_port} -- it port-walked away from a conflict that may have "
        "since cleared",
        data,
    )


def _argv0_tokens(argv0: str) -> set[str]:
    """Tokens of a command name: itself plus its dash/space/dot pieces, so
    "code-insiders" and "Code Helper" both match the "code" marker."""
    argv0 = argv0.lower()
    if argv0.endswith(".exe"):
        argv0 = argv0[:-4]
    if not argv0:
        return set()
    pieces = argv0.replace("_", "-").replace(".", "-").replace(" ", "-").split("-")
    return {argv0, *(piece for piece in pieces if piece)}


def _name_tokens(cmdline: str) -> frozenset[str]:
    """Lowercase name tokens of a cmdline for client detection: argv0's
    basename with its pieces, and full basenames of later executable-looking
    args (so a client launched as ``node …/pi`` is found by its script name).
    Full-token matching only — substrings would make any path under a
    ``code/`` directory look like a VS Code client."""
    tokens: set[str] = set()
    fields = cmdline.split()
    if fields:
        tokens.update(_argv0_tokens(fields[0].replace("\\", "/").rsplit("/", 1)[-1]))
    for field in fields[1:]:
        if field.startswith("-"):
            continue
        base = field.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base.endswith(".exe"):
            base = base[:-4]
        if base:
            tokens.add(base)
            tokens.add(base.split(".", 1)[0])
    return frozenset(tokens)


def _detect_client(follower_pid: int, table: list[tuple[int, int, str]]) -> ClientReconnect | None:
    """The first ``RECONNECT_STEPS`` entry whose markers match an ancestor of
    the follower (a follower's own argv is always octowright's). Walks parents
    only, so the MCP client that spawned the follower is named rather than an
    unrelated process; a dead or reparented owner ends the walk unidentified."""
    by_pid = {pid: (ppid, cmd) for pid, ppid, cmd in table}
    node = by_pid.get(follower_pid)
    if node is None:
        return None
    ppid, _ = node
    for _ in range(16):
        parent = by_pid.get(ppid)
        if parent is None:
            return None
        p_ppid, p_cmd = parent
        tokens = _name_tokens(p_cmd)
        for entry in RECONNECT_STEPS:
            if any(marker in tokens for marker in entry.markers):
                return entry
        if p_ppid <= 1 or p_ppid == ppid:
            return None
        ppid = p_ppid
    return None


def _as_text(value: Any) -> str | None:
    """A non-empty string field from an untyped snapshot, else None."""
    return value if isinstance(value, str) and value else None


def _as_str_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _snapshot_row(pid_key: str, snap: Any, leader: str) -> dict[str, Any] | None:
    """One live follower's flag row, or None when the entry isn't one.

    Liveness matches ``summarize_state``'s partition (dead followers are not
    reported anywhere). "Throttled" reads the snapshot's ``last_error``: both
    the pre-fix ExceptionGroup wrapping a 429 and the post-fix named throttle
    text report it, so the row flags either codebase."""
    from octowright.bridge_state import UNKNOWN_FOLLOWER_VERSION

    if not isinstance(snap, dict):
        return None
    try:
        pid = int(pid_key)
    except (TypeError, ValueError):
        return None
    if not pid_is_alive(pid):
        return None
    version = _as_text(snap.get("follower_version")) or UNKNOWN_FOLLOWER_VERSION
    last_error = _as_text(snap.get("last_error"))
    mismatch = _as_str_list(snap.get("handshake_mismatch"))
    stale = version != leader
    throttled = last_error is not None and ("throttl" in last_error.lower() or "429" in last_error)
    return {
        "pid": pid,
        "version": version,
        "stale": stale,
        "handshake_mismatch": mismatch or None,
        "throttled": throttled,
        "last_error": last_error,
        "needs_attention": stale or bool(mismatch) or throttled,
    }


def _follower_rows(state: dict[str, Any], leader: str, table: list[tuple[int, int, str]]) -> list[dict[str, Any]]:
    """Per-live-follower detail rows for the check's data and remedy clause."""
    followers = state.get("followers") if isinstance(state, dict) else None
    rows: list[dict[str, Any]] = []
    if not isinstance(followers, dict):
        return rows
    for pid_key, snap in followers.items():
        row = _snapshot_row(pid_key, snap, leader)
        if row is None:
            continue
        if row["needs_attention"]:
            entry = _detect_client(row["pid"], table)
            row["client"] = (entry.detect_label or entry.name) if entry else None
            row["reconnect_step"] = client_step(entry) if entry else _UNKNOWN_CLIENT_STEP
        rows.append(row)
    return rows


def _remedy_clause(rows: list[dict[str, Any]]) -> str:
    """ "; pid N (<client>): <reconnect step>" for the flagged rows, capped at 3.

    Every flagged follower names its own owner and step: the remediation is
    per-client and this clause is the whole point of the check."""
    flagged = [r for r in rows if r.get("needs_attention")]
    if not flagged:
        return ""
    bits = [f"pid {r['pid']} ({r.get('client') or 'client unknown'}): {r['reconnect_step']}" for r in flagged[:3]]
    extra = len(flagged) - 3
    if extra > 0:
        bits.append(f"+{extra} more")
    return " — " + "; ".join(bits)


async def check_followers() -> Check:
    """Do the live followers run the same code as the daemon answering for them?

    A follower is a subprocess its MCP client owns, and it deliberately SURVIVES
    a leader restart so the client is not dropped. The consequence is that
    upgrading octowright and restarting the daemon updates the leader and
    nothing else: every connected client keeps running whatever follower it
    spawned, until that client reconnects. Observed here with followers two
    releases behind a current leader, driving browsers, with `doctor` reporting
    all-PASS -- because nothing in doctor looked at followers at all.

    Compared against the RUNNING DAEMON's version rather than this process's
    ``VERSION``: doctor is usually invoked from a checkout that has already been
    upgraded, so its own version is what the daemon *will* be after a restart,
    not what is answering now. Using it would report skew against a version
    nobody is running.

    A warn, never a fail: skew is a deployment state the operator resolves per
    client, not a broken machine, and `--fix` cannot touch it (killing a
    follower just breaks that client's session -- the client does not respawn a
    dead stdio server).
    """
    from octowright import defaults
    from octowright.bridge_state import read_state, stale_follower_count, summarize_state

    leader = await _running_leader_version()
    if leader is None:
        return Check("followers", "skip", "no daemon answering — nothing to compare followers against", {})

    state = read_state(defaults.BRIDGE_STATE_PATH)
    summary = summarize_state(state)
    versions: dict[str, int] = summary.get("follower_versions") or {}
    live = summary.get("follower_count", 0)
    dead = summary.get("dead_follower_count", 0)
    # Per-follower rows tie each flag to the client that must act on it:
    # the remediation is per-client (only ITS reconnect spawns a fresh
    # follower), so the check names the client and its step instead of
    # repeating one generic line. table is read once for all detections.
    from octowright.process_reaper import list_processes

    rows = _follower_rows(state, leader, list_processes())
    # summarize_state compares against THIS process's VERSION; recompute against
    # the daemon that is actually answering, through the same shared predicate
    # so only the baseline differs.
    stale = stale_follower_count(versions, leader)
    # A client can hold an ``initialize`` answer the running leader would not
    # give even at the same version (see the bridge summary's hint); absent in
    # a summary from before the field existed.
    mismatched = summary.get("handshake_mismatch_count") or 0
    mismatch_fields: dict[str, int] = summary.get("handshake_mismatch_fields") or {}
    throttled_rows = [row for row in rows if row["throttled"]]
    data = {
        "leader_version": leader,
        "follower_versions": versions,
        "live_followers": live,
        "stale_followers": stale,
        "dead_followers_ignored": dead,
        "handshake_mismatches": mismatched,
        "handshake_mismatch_fields": mismatch_fields,
        "throttled_followers": len(throttled_rows),
        "followers": rows,
    }
    if not live:
        return Check("followers", "ok", f"no live followers (leader {leader})", data)
    status, detail = _followers_verdict(
        leader=leader,
        live=live,
        versions=versions,
        stale=stale,
        mismatched=mismatched,
        mismatch_fields=mismatch_fields,
        throttled_rows=throttled_rows,
    )
    return Check("followers", status, detail + _remedy_clause(rows), data)


def _followers_verdict(
    *,
    leader: str,
    live: int,
    versions: dict[str, int],
    stale: int,
    mismatched: int,
    mismatch_fields: dict[str, int],
    throttled_rows: list[dict[str, Any]],
) -> tuple[Status, str]:
    """``(status, detail)`` for the followers check, first problem wins:
    skew, then handshake drift, then the leader's 429 loop against a follower.
    The per-client remedy clause is appended by the caller for any of these."""
    if stale:
        spread = ", ".join(f"{v} x{c}" for v, c in sorted(versions.items()) if v != leader)
        return (
            "warn",
            f"{stale} of {live} live follower(s) behind leader {leader} ({spread}) — "
            "each client must reconnect; a daemon restart cannot update them",
        )
    if mismatched:
        fields = ", ".join(sorted(mismatch_fields))
        return (
            "warn",
            f"{mismatched} of {live} live follower(s) hold a client handshake that differs from "
            f"leader {leader} ({fields}) — each client must reconnect to pick up the leader's",
        )
    if throttled_rows:
        return (
            "warn",
            f"{len(throttled_rows)} of {live} live follower(s) are bouncing off the leader's "
            "new-session rate limit (HTTP 429 loop) — a structural follower/leader mismatch, not "
            "load; only each client's reconnect fixes it",
        )
    return "ok", f"all {live} live follower(s) on leader version {leader}"


async def _running_leader_version() -> str | None:
    """Version reported by the daemon currently answering, or None if none is."""
    import httpx2

    from octowright.singleton import pid_is_alive, read_lock

    info = read_lock()
    if info is None or not pid_is_alive(info.pid):
        return None
    try:
        async with httpx2.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"http://{info.http_host}:{info.http_port}/api/health")
        if response.status_code != 200:
            return None
        body = response.json()
    except (httpx2.HTTPError, OSError, ValueError):
        return None
    version = body.get("version") if isinstance(body, dict) else None
    return version if isinstance(version, str) and version else None
