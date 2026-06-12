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
