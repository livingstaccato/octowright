# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Correlate renderer-crash incidents with macOS ``.ips`` DiagnosticReports.

When a ``chrome-headless-shell`` renderer dies with a ``SIGSEGV`` the OS writes a
``*.ips`` crash report to ``~/Library/Logs/DiagnosticReports/`` — but a second or
two AFTER the crash, while ``incidents.record`` stamps the renderer-crash incident
~60ms in. So the real signal/exception signature can't be attached at crash time;
it has to be looked up at **status-read time**, by matching the incident timestamp
against nearby ``.ips`` file mtimes (a short window, browser-process names only).

macOS-only and best-effort: on any other platform, or if nothing matches, the
incident is returned unchanged. This module never raises into a status call.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from octowright.browser_pool.incidents import CATEGORY_RENDERER_CRASH

# Default macOS crash-report directory. Per-user; overridable for tests.
_DEFAULT_REPORTS_DIR = Path.home() / "Library" / "Logs" / "DiagnosticReports"

# Filename substrings that mark a .ips as a managed-browser crash (case-insensitive).
# Covers chrome-headless-shell, Chromium, Google Chrome for Testing, Firefox,
# WebKit, and the Playwright driver children.
_BROWSER_TOKENS = ("chrome", "chromium", "firefox", "webkit", "playwright", "plugin-container")

# Time window around the incident timestamp in which a .ips is considered the
# same event. The OS lags the crash by ~1-2s (so most of the budget is AFTER);
# a small BEFORE margin tolerates clock jitter between the recorder and the FS.
_WINDOW_AFTER_SECONDS = 30.0
_WINDOW_BEFORE_SECONDS = 5.0


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _parse_ts(iso_z: str) -> float | None:
    """Parse an incident's ISO-8601 ``...Z`` timestamp into an epoch float."""
    try:
        return datetime.fromisoformat(iso_z.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _name_is_browser_crash(name: str) -> bool:
    low = name.lower()
    return any(token in low for token in _BROWSER_TOKENS)


def _parse_ips(path: Path) -> dict[str, Any] | None:
    """Extract ``{path, app_name, signal, type}`` from a ``.ips`` file.

    The modern ``.ips`` format is a one-line JSON header followed by a JSON body;
    the body's ``exception`` block carries ``signal`` (e.g. ``SIGSEGV``) and
    ``type`` (e.g. ``EXC_BAD_ACCESS``). Returns ``None`` on any malformed file.
    """
    try:
        text = path.read_text(errors="replace")
        newline = text.index("\n")
        header = json.loads(text[:newline])
        body = json.loads(text[newline + 1 :])
    except (OSError, ValueError):
        return None
    exc_block = body.get("exception") if isinstance(body, dict) else None
    exc_block = exc_block if isinstance(exc_block, dict) else {}
    return {
        "path": str(path),
        "app_name": header.get("app_name") if isinstance(header, dict) else None,
        "signal": exc_block.get("signal"),
        "type": exc_block.get("type"),
    }


def _nearest_ips(directory: Path, crash_epoch: float) -> Path | None:
    """The browser-crash ``.ips`` in ``directory`` whose mtime is closest to
    ``crash_epoch`` within the window, or ``None``. Sorted glob → deterministic
    scan order (stable nearest-match selection, testable tie-breaking)."""
    best: tuple[float, Path] | None = None
    for path in sorted(directory.glob("*.ips")):
        if not _name_is_browser_crash(path.name):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if crash_epoch - _WINDOW_BEFORE_SECONDS <= mtime <= crash_epoch + _WINDOW_AFTER_SECONDS:
            delta = abs(mtime - crash_epoch)
            if best is None or delta < best[0]:
                best = (delta, path)
    return best[1] if best is not None else None


def find_crash_report(crash_ts: str, *, reports_dir: Path | None = None) -> dict[str, Any] | None:
    """Return the parsed ``.ips`` whose mtime is closest to ``crash_ts`` within the
    correlation window, or ``None`` (non-macOS, no dir, bad ts, or no match)."""
    if not _is_macos():
        return None
    directory = Path(reports_dir) if reports_dir is not None else _DEFAULT_REPORTS_DIR
    if not directory.is_dir():
        return None
    crash_epoch = _parse_ts(crash_ts)
    if crash_epoch is None:
        return None
    nearest = _nearest_ips(directory, crash_epoch)
    return _parse_ips(nearest) if nearest is not None else None


def enrich(incidents: list[dict[str, Any]], *, reports_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return ``incidents`` with a ``crash_report`` attached to each renderer-crash
    record that correlates to a ``.ips`` file. Non-macOS is a pass-through (the same
    list object); matched records are copied (originals are not mutated)."""
    if not _is_macos():
        return incidents
    out: list[dict[str, Any]] = []
    for inc in incidents:
        if inc.get("category") == CATEGORY_RENDERER_CRASH and "crash_report" not in inc and inc.get("ts"):
            report = find_crash_report(inc["ts"], reports_dir=reports_dir)
            if report is not None:
                inc = {**inc, "crash_report": report}
        out.append(inc)
    return out
