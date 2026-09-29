# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Prune a Chromium profile's download history before opening it.

**The crash this avoids.** Chrome for Testing 153.0.8010.12 -- ``chromium-1243``,
the build Playwright 1.63 installs, and therefore what a fresh install of
octowright resolves to -- kills its own *browser* process on the first download
of a headed run whenever the profile already holds a download-history row and
Playwright is controlling downloads. It is a use-after-free on the UI thread
(``Received signal 11 SI_KERNEL ... General Protection Fault``, with the
registers holding PartitionAlloc's ``0xcd`` freed-memory byte), not a CHECK, so
there is no message to quote. It reaches octowright as the whole browser
vanishing about 0.3s after the download starts.

Measured with raw Playwright (no octowright code in the process), a headed
persistent context, and one download per fresh browser process:

======================================================  ==========
condition                                               crashes
======================================================  ==========
profile holds 1+ download rows (blob ``a.download``)    7/7
same, server ``Content-Disposition: attachment``        5/5
same, navigation to a blob URL                          5/5
same, 10s idle between launch and download              4/5
``downloads`` rows deleted, all other tables kept       0/6
whole ``History`` file deleted                          0/6
empty profile (its first download ever)                 0/1 per run
headless                                                0/6
``accept_downloads=False``                              0/5
``chromium-1234`` (Chrome 151), same history            0/5
======================================================  ==========

Rewriting the rows instead of deleting them does not help -- ``danger_type=0``,
``transient=1``, an interrupted ``state``, a back-dated ``end_time``, and making
every ``target_path`` exist all still crashed 5/5 -- so the trigger is a row in
``History`` when the browser starts, and removing the rows is the repair. The
field data agrees: every one of six crashes in the operator's recordings was the
FIRST download of its browser process, and none of ~520 later downloads in a
process ever failed. Within one process a download after the first is safe.

**It has to run on every launch.** Chromium re-inserts the rows itself during
startup from its own download database (``shared_proto_db``): a profile pruned to
0 rows held 26 again after one download-free headed run. The re-inserted rows do
not trigger the crash -- what matters is what ``History`` holds when the browser
opens it -- so the prune runs before each launch rather than once. Measured with
it on ``chromium-1243``: 0 crashes in 30 relaunches (90/90 downloads) through
``BrowserPool`` and 25/25 with raw Playwright; with it off, 27/27 relaunches
crashed in the same harness.

**Why deleting is acceptable.** Octowright always launches with
``accept_downloads=True``, so Chromium writes every download it records to
Playwright's per-context ``playwright-artifacts-*`` temp directory, which
Playwright deletes when the context closes. Every row in an octowright profile
therefore points at a file that no longer exists; the saved copy octowright
keeps is under the recordings root, recorded in the session JSONL. And because
Chromium re-inserts the rows from its own store, ``chrome://downloads`` keeps
its list anyway. Browsing history (``urls``/``visits``) is not touched.

**Scope.** Chromium persistent profiles only (a ``session`` tmpdir and an
ephemeral context start empty). Firefox and WebKit profiles hold no ``History``
database. Same shape and the same guards as
:mod:`octowright.browser_pool.restore_prompt`: it refuses to write a profile
whose singleton lock is still present after the stale-lock prune, and it is
best-effort -- a History file that cannot be opened or written is logged and
left alone, because a cleanup must never be the reason a launch fails.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Final

from provide.telemetry import get_logger

from octowright.browser_pool.singleton_locks import profile_lock_present

log = get_logger(__name__)

PRUNE_DOWNLOAD_HISTORY_ENV: Final = "OCTOWRIGHT_PRUNE_DOWNLOAD_HISTORY"

# Same spelling as restore_prompt / private_paths: only an explicit token opts
# out, so an empty value still means on.
_OFF: Final[frozenset[str]] = frozenset({"0", "off", "false", "no", "never", "none", "disabled"})

_HISTORY: Final = "History"

# Child tables first: they reference ``downloads.id``. Fixed identifiers, never
# caller data -- they are interpolated only because SQLite cannot bind a table
# name.
_DOWNLOAD_TABLES: Final = ("downloads_url_chains", "downloads_slices", "downloads")

# Chromium holds the database exclusively only while it runs, and the lock
# check above keeps us away from a running one; this bounds the rare wait on a
# hot journal instead of hanging a launch on it.
_SQLITE_TIMEOUT_SECONDS: Final = 2.0


def prune_download_history_enabled() -> bool:
    """Whether to delete a Chromium profile's download rows before launch.

    Default ON. Opt out with ``OCTOWRIGHT_PRUNE_DOWNLOAD_HISTORY`` set to a
    falsey token -- an escape hatch should a future Chromium schema make the
    write unsafe, or to reproduce the crash on purpose.
    """
    return os.environ.get(PRUNE_DOWNLOAD_HISTORY_ENV, "on").strip().lower() not in _OFF


def _prune_one(history: Path) -> None:
    try:
        con = sqlite3.connect(f"file:{history}?mode=rw", uri=True, timeout=_SQLITE_TIMEOUT_SECONDS)
    except sqlite3.Error as exc:
        log.debug("browser.download_history_unopenable", path=str(history), error=str(exc))
        return
    try:
        present = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'downloads%'")
        }
        if "downloads" not in present:
            return
        rows = int(con.execute("SELECT count(*) FROM downloads").fetchone()[0])
        if rows == 0:
            return
        with con:
            for table in _DOWNLOAD_TABLES:
                if table in present:
                    con.execute(f"DELETE FROM {table}")  # nosec B608 - fixed identifiers, see _DOWNLOAD_TABLES
        log.info("browser.download_history_pruned", path=str(history), rows=rows)
    except sqlite3.Error as exc:
        # Corrupt, locked past the timeout, or a schema we do not recognise.
        log.warning("browser.download_history_prune_failed", path=str(history), error=str(exc))
    finally:
        con.close()


def prune_download_history(user_data_dir: Path) -> None:
    """Delete the download rows of every profile under *user_data_dir*.

    Call it immediately before ``launch_persistent_context`` for Chromium, after
    ``prune_stale_singleton_locks``, exactly where ``clear_crash_restore_prompt``
    runs and for the same reason: the browser this call is about to start is not
    running yet, and a lock that survived the stale-lock prune means some other
    process is, so the database is left alone.
    """
    if not prune_download_history_enabled():
        return
    if profile_lock_present(user_data_dir):
        log.debug("browser.download_history_skipped_profile_in_use", path=str(user_data_dir))
        return
    try:
        candidates = sorted(user_data_dir.glob(f"*/{_HISTORY}"))
    except OSError as exc:
        log.debug("browser.download_history_unscannable", path=str(user_data_dir), error=str(exc))
        return
    for history in candidates:
        if history.is_file():
            _prune_one(history)


__all__ = ["PRUNE_DOWNLOAD_HISTORY_ENV", "prune_download_history", "prune_download_history_enabled"]
