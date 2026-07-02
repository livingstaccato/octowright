# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Post-upgrade "what's new" notice.

Detects the first run after Octowright is updated (current version differs from
the last-seen version on disk), surfaces curated, human-friendly highlights, and
renders a banner. The leader records the notice at startup so `octowright_status`
can hand it to the agent — Octowright's standard status-first banner flow — and
also echoes it to stderr (a human terminal in inline mode, the daemon log
otherwise).

The curated `HIGHLIGHTS` are intentionally separate from CHANGELOG.md: the
changelog is the technical record; these are the snappy "why it's cool" lines,
updated by hand at release time.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from provide.telemetry import get_logger

from octowright._paths import atomic_write_text
from octowright.config_paths import user_config_dir
from octowright.version import VERSION

log = get_logger(__name__)

# Where the last-seen version marker lives. Same config-dir convention as the
# Advisor state; override for tests / ops via OCTOWRIGHT_UPGRADE_STATE.
UPGRADE_STATE_PATH = Path(os.environ.get("OCTOWRIGHT_UPGRADE_STATE", str(user_config_dir() / "upgrade.json")))

# Curated highlights keyed by version. Add a new entry at release time — keep
# each line short and benefit-first ("why it's cool"), not a raw changelog dump.
HIGHLIGHTS: dict[str, list[str]] = {
    "0.12.0": [
        "No more false 'Octowright disconnected' on slow tool calls: the leader now heartbeats progress "
        "for every in-flight call, so a slow-but-alive browser action keeps its bridge deadline alive "
        "instead of timing out — a genuinely wedged leader still fails fast.",
        "Proactive notifications reach you in the default daemon setup: crash / recovery / driver-death / "
        "session-closed now stream to stdio clients (Codex, Claude Code) over the leader's new "
        "/api/mcp-events channel, not just in inline mode.",
        "The bridge no longer storms or splits: a leader that instantly ends a session is backed off "
        "instead of hot-looped into a transport storm, and a respawn can't spin up a second competing "
        "daemon beside a healthy one.",
    ],
    "0.11.0": [
        "Cheaper browsing loops: new compact browser and HTTP-first discovery tools help agents "
        "find links, fields, and page outlines before paying for full snapshots or raw dumps.",
        "Bounded summaries by default: console, network, downloads, captures, and text reads now "
        "surface concise next actions so agents can drill in only when needed.",
        "Live preview is more resilient: the dashboard falls back from screencast streaming to "
        "snapshot polling when a browser cannot provide live frames.",
    ],
    "0.10.1": [
        "Survives a compaction freeze: when your MCP client (Codex/Claude) pauses to compact and "
        "freezes Octowright's follower, the bridge no longer times out and dies on resume — it "
        "detects the suspension, keeps in-flight calls alive, and re-handshakes the leader cleanly "
        "instead of stranding on a half-initialized session. No more 'Octowright timed out' after a compaction.",
        "Terminal connectors now enumerate in the canonical ssh, telnet, pty order; the terminal_launch "
        "kind arg and behavior are unchanged.",
    ],
    "0.10.0": [
        "Terminals, alongside browsers: the optional octowright[terminal] extra adds in-process "
        "PTY / SSH / telnet sessions that record to the same JSONL and show up in the dashboard "
        "with a live, read-only xterm.js screen — new terminal_* tools and scenario participants.",
        "Browsers self-heal: a crashed renderer is replaced in place (not a broken reload) and a "
        "dead shared Playwright driver rebuilds itself instead of bricking the pool. Crashes, driver "
        "deaths, and lost sessions now surface as MCP notifications + incident records, with a health "
        "verdict in octowright_status.",
        "Hardened by default, configurable where it changes behavior: the loopback /mcp transport "
        "requires a capability token, recordings are written 0600, navigation can block SSRF to "
        "internal/cloud-metadata hosts, downloads are contained, and credentials are scrubbed from "
        "traces and selector-less sinks. See the OCTOWRIGHT_* knobs in the docs.",
        "Runs on Windows now: a real Win32 RSS reader for the memory governor + telemetry, "
        "cross-platform paths, and security bumps for cryptography / starlette / python-multipart / "
        "msgpack / pydantic-settings.",
    ],
    "0.9.1": [
        "No more mid-session disconnects: the idle watchdog is now OFF by default, so "
        "the daemon stays up across client blips instead of auto-exiting and tearing down "
        "your browsers. Opt back into auto-quit with OCTOWRIGHT_IDLE_GRACE on CI/shared hosts.",
        "Reconnects are seamless: a detached daemon reliably stays alive after a client "
        "disconnects (no more fragile inline-mode fallback), and --keep-alive now actually "
        "reaches the daemon it's meant to govern.",
        "No more orphaned `octowright serve` processes: a follower hard-exits when its MCP "
        "client closes stdin, instead of lingering and reconnecting forever.",
    ],
    "0.9.0": [
        "Crashed browsers are caught: a renderer crash (Aw, Snap) pushes a browser_crashed "
        "notification and a clear 'crashed — relaunch' message instead of an opaque failure.",
        "Bridge blips no longer double-run side-effectful calls: an in-flight tool call is "
        "safely auto-resumed after a reconnect (leader-side idempotency), so a browser_launch "
        "interrupted mid-flight resumes as one browser, not two.",
        "Long macros don't spuriously time out: macro_run streams progress per step (which "
        "keeps the bridge alive), and a failure tells you exactly which steps already landed.",
        "Quieter, cleaner telemetry: the per-call OpenTelemetry error that spammed the daemon "
        "log is gone, and HTTP metrics now export over OTLP (the /api/metrics scrape endpoint "
        "is removed — point a collector at the process).",
    ],
    "0.8.0": [
        "Self-healing macros: macro_repair_apply rewrites a brittle CSS selector into its "
        "semantic click_by/fill_by (from the role/label/text captured at record time) and "
        "saves it in place — no hand-editing JSON.",
        "Snapshots follow you into iframes: after browser_switch_frame, browser_snapshot and "
        "browser_brief show the frame you're in, not the parent page (capture / golden / "
        "read_markdown too).",
        "Disconnect-aware: if octowright's MCP server drops, you're told to reconnect it in "
        "your client instead of silently opening a browser it can't drive, inspect, or record.",
        "This 'what's new' notice itself — shown once on the first run after an update, and in octowright_status.",
    ],
    "0.7.0": [
        "Launches open instantly on a local /new-tab page (Otto + a live status strip) "
        "instead of hitting the network — works fully offline.",
        "Cmd+T (Ctrl+T) lands on /new-tab across Chromium, Firefox, and WebKit.",
        "No-argument launches auto-name the browser and persist a profile from your git "
        "repo / username / .octowright config — no more random instance IDs.",
        "browser_each fans one action (navigate/resize/evaluate/wait_for/screenshot) across every browser at once.",
        "Dashboard moved to port 6286 ('OCTO'); octowright restart now rebinds in ~2s.",
        "browser_click / browser_fill take ARIA locators (role/label/text/test_id) "
        "directly — the separate _by tools are gone.",
    ],
}


class UpgradeNotice(TypedDict):
    kind: str  # "install" (no prior version) | "upgrade" (version changed)
    previous_version: str | None
    current_version: str
    highlights: list[str]


def load_last_seen(path: Path | None = None) -> str | None:
    """Return the last-seen version recorded on disk, or None if unset/unreadable."""
    p = path or UPGRADE_STATE_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("last_seen_version")
    return version if isinstance(version, str) else None


def save_last_seen(version: str, path: Path | None = None) -> None:
    """Atomically persist ``version`` as the last-seen version."""
    p = path or UPGRADE_STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps({"last_seen_version": version}, indent=2), encoding="utf-8")


def compute_upgrade(current: str, last_seen: str | None) -> UpgradeNotice | None:
    """Return a notice when ``current`` differs from ``last_seen``, else None.

    ``last_seen is None`` (no marker yet) is treated as a fresh install; any other
    mismatch is an upgrade carrying the prior version.
    """
    if last_seen == current:
        return None
    return {
        "kind": "install" if last_seen is None else "upgrade",
        "previous_version": last_seen,
        "current_version": current,
        "highlights": HIGHLIGHTS.get(current, []),
    }


def render_banner(notice: UpgradeNotice) -> str:
    """Render a human-facing banner for a notice (stderr / daemon log)."""
    current = notice["current_version"]
    if notice["kind"] == "install":
        head = f"Welcome to Octowright {current}!"
    else:
        head = f"Octowright updated {notice['previous_version']} -> {current}"
    lines = [head, "What's new:"]
    lines += [f"  - {h}" for h in notice["highlights"]]
    lines.append("Full notes: CHANGELOG.md  -  call octowright_status for details.")
    return "\n".join(lines)


def announce_upgrade_if_changed(
    *,
    set_notice: Callable[[dict[str, Any]], None],
    echo: Callable[[str], None],
    current: str | None = None,
    path: Path | None = None,
) -> UpgradeNotice | None:
    """First-run-after-update orchestration, called once by the leader at startup.

    Computes the notice, records it (``set_notice`` — for octowright_status),
    echoes the banner (``echo`` — stderr/log), and marks the current version seen
    so subsequent same-version runs are silent. Sole writer of the marker → no
    races. ``current``/``path`` default to the live version and state file;
    overridable for tests. Returns the notice, or None when nothing changed.
    """
    cur = current or VERSION
    notice = compute_upgrade(cur, load_last_seen(path))
    if notice is None:
        return None
    set_notice(dict(notice))
    echo(render_banner(notice))
    save_last_seen(cur, path)
    return notice
