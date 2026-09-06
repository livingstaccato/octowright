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
updated by hand at release time. Each entry is a `Highlight` -- a short `title`
plus the `body` paragraph -- so the same data can headline a blog post without
a second, hand-synchronised copy of the text.

They live as one JSON file per version under ``highlights/`` rather than as a
dict literal here, and the reason is a hard constraint rather than taste: 118
titled entries project to roughly 1,160 lines, well past the repository's
777-LOC ceiling, and no split of a single Python module escapes that for long.
One file per version also means a release adds a file instead of editing the
top of a shared one, so two release branches never conflict over it, and a
blog renderer is a directory glob rather than a parser. They are read eagerly
at import (measured: 48 KB across 40 files, 0.06 ms) so ``HIGHLIGHTS`` stays a
plain dict and every existing call site is unchanged.
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

#: Directory of per-version highlight files (``0.20.0.json`` -> a list of
#: ``Highlight``). Shipped in the wheel; ``scripts/check_wheel_assets.py``
#: carries a tripwire so a packaging change cannot silently drop them.
HIGHLIGHTS_DIR = Path(__file__).resolve().parent / "highlights"


class Highlight(TypedDict):
    """One curated release note: a headline plus the paragraph under it."""

    title: str
    body: str


def _version_key(path: Path) -> tuple[int, ...]:
    """Sort key for a ``<version>.json`` filename, as a numeric tuple.

    A non-numeric or malformed stem sorts last (as ``(-1,)``) rather than
    raising, so one stray file cannot stop the daemon from starting.
    """
    try:
        return tuple(int(part) for part in path.stem.split("."))
    except ValueError:
        return (-1,)


def _load_highlights() -> dict[str, list[Highlight]]:
    """Read every ``highlights/<version>.json`` into a version-keyed dict.

    Deliberately tolerant: a file that is missing, unreadable or malformed is
    logged and skipped rather than raised. This runs at import, in the daemon's
    startup path, and a corrupt cosmetic release note must not stop the daemon
    from starting -- the worst case is a version whose banner is empty, which
    ``tests/test_upgrade.py`` catches at release time for the shipped VERSION.
    """
    loaded: dict[str, list[Highlight]] = {}
    if not HIGHLIGHTS_DIR.is_dir():
        log.warning("octowright.upgrade.highlights_dir_missing", path=str(HIGHLIGHTS_DIR))
        return loaded
    # Newest first, and sorted by VERSION rather than by filename: the dict
    # literal this replaced was hand-ordered newest-first, and a release guard
    # asserts the first key is the current VERSION. A lexical sort silently
    # breaks that -- "0.10.0" < "0.7.0" as strings -- so the ordering is
    # computed from the parsed version tuple instead.
    for path in sorted(HIGHLIGHTS_DIR.glob("*.json"), key=_version_key, reverse=True):
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("octowright.upgrade.highlights_unreadable", path=str(path), error=repr(exc))
            continue
        if isinstance(entries, list):
            loaded[path.stem] = entries
        else:
            log.warning("octowright.upgrade.highlights_not_a_list", path=str(path))
    return loaded


#: Curated highlights keyed by version, loaded from ``HIGHLIGHTS_DIR``. Add a
#: release by writing ``highlights/<version>.json`` -- keep each title a short
#: benefit-first headline and each body a paragraph, not a changelog dump.
HIGHLIGHTS: dict[str, list[Highlight]] = _load_highlights()


class UpgradeNotice(TypedDict):
    kind: str  # "install" (no prior version) | "upgrade" (version changed)
    previous_version: str | None
    current_version: str
    highlights: list[Highlight]


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
    # Titles only. The bodies open with the same claim the title condenses, so
    # rendering both reads as a stutter -- and five ~500-char paragraphs in a
    # daemon log is not a banner. The full text stays in the notice, which is
    # what octowright_status hands the agent, and the closing line already
    # points at both.
    lines += [f"  - {h['title']}" for h in notice["highlights"]]
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
