# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Clear Chromium's "Restore pages?" prompt before opening a persistent profile.

Chromium records how the previous run of a profile ended in
``<user-data-dir>/<profile>/Preferences`` under ``profile.exit_type``. Anything
other than ``Normal`` opens the next launch with a "Chrome didn't shut down
correctly -- Restore pages?" bubble over the page, and the flag is **sticky**:
it is only rewritten when a run of that same profile exits cleanly. An
agent-driven browser frequently never does, so one dirty exit prompts on every
launch of that persona from then on.

Octowright is a reliable source of dirty exits, which is why this is its problem
to clean up rather than the operator's:

* the headed-Chromium browser-process abort under investigation kills the whole
  browser, not a renderer;
* ``octowright restart`` SIGKILLs the leader, and browsers die with their driver;
* the orphan reaper kills browsers whose driver is already gone.

Two of a real machine's 27 persona profiles were sitting at ``Crashed`` when
this was written, and the bubble covers the page the caller just navigated to --
so it is not merely cosmetic for a tool whose next step is usually a click.

This sits beside :mod:`octowright.browser_pool.singleton_locks` and is the same
shape of problem: a browser that died without an orderly shutdown leaves state
behind that degrades every future launch of that profile, and the repair is safe
only because it runs before the profile is opened.

**Why not ``--hide-crash-restore-bubble``.** Chromium has a flag for it, but
arbitrary argv reaches ``LaunchOptions.launch_args``, which is gated behind
``OCTOWRIGHT_ALLOW_EXECUTABLE_PATH`` -- a code-execution opt-in, and far too
heavy a door to open just to suppress a prompt. It would also only hide the
bubble while leaving the profile marked crashed, so the state stays dirty and
anything else reading it still sees a browser that never shut down.

**Scope.** Persistent profiles only. An ephemeral session gets a fresh
user-data-dir per launch and has no previous run to have crashed, so there is
nothing to clear and nothing to write. Chromium-family only in practice --
Firefox and WebKit have no such file, and the glob simply finds nothing.

Best-effort throughout: a profile that cannot be read or written is logged and
left alone. Suppressing a prompt must never be the reason a launch fails.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

from provide.telemetry import get_logger

from octowright._paths import atomic_write_text
from octowright.browser_pool.singleton_locks import profile_lock_present

log = get_logger(__name__)

RESTORE_PROMPT_ENV: Final = "OCTOWRIGHT_SUPPRESS_RESTORE_PROMPT"

# Shared spelling with private_paths.PRIVATE_OFF: only an explicit token opts
# out, so an empty value still means on.
_OFF: Final[frozenset[str]] = frozenset({"0", "off", "false", "no", "never", "none", "disabled"})

# What Chromium writes when the run ended properly. Every other value it uses
# ("Crashed", "SessionEnded", ...) prompts, so the check is against this one
# rather than a list of the failures -- a spelling we have not seen must be
# treated as dirty, not silently accepted as clean.
_CLEAN_EXIT: Final = "Normal"

_PREFERENCES: Final = "Preferences"


def suppress_restore_prompt() -> bool:
    """Whether to clear a profile's crash flag before opening it.

    Default ON. Opt out with ``OCTOWRIGHT_SUPPRESS_RESTORE_PROMPT`` set to a
    falsey token -- for a headed profile a human shares, where being offered the
    tabs back after a crash is the wanted behaviour.
    """
    return os.environ.get(RESTORE_PROMPT_ENV, "on").strip().lower() not in _OFF


def _repaired(profile: dict[str, Any]) -> bool:
    """Set the clean-exit keys on *profile*; report whether anything changed.

    ``exited_cleanly`` is the older spelling and is still honoured by Chromium
    when present. It is repaired only when the profile already carries it:
    adding a key Chromium did not write means guessing at a schema we do not
    own, and the profiles observed in the field carry ``exit_type`` alone.
    """
    changed = False
    if profile.get("exit_type") != _CLEAN_EXIT:
        profile["exit_type"] = _CLEAN_EXIT
        changed = True
    if "exited_cleanly" in profile and profile["exited_cleanly"] is not True:
        profile["exited_cleanly"] = True
        changed = True
    return changed


def _clear_one(prefs: Path) -> None:
    """Rewrite one ``Preferences`` file if its profile is marked unclean."""
    try:
        body = json.loads(prefs.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        # A profile mid-write, truncated by a kill, or simply not ours.
        log.debug("browser.restore_prompt_unreadable", path=str(prefs), error=str(exc))
        return
    if not isinstance(body, dict):
        log.debug("browser.restore_prompt_unexpected_shape", path=str(prefs))
        return
    profile = body.get("profile")
    if not isinstance(profile, dict):
        # A fresh profile Chromium has not finished populating.
        return
    if not _repaired(profile):
        return
    try:
        # Atomic, and mode-preserving: this file lives inside a tree
        # OCTOWRIGHT_PROFILES_PRIVATE locks to 0700, and a plain write would
        # be a torn file if the process dies mid-rewrite.
        atomic_write_text(prefs, json.dumps(body))
    except OSError as exc:
        log.warning("browser.restore_prompt_unwritable", path=str(prefs), error=str(exc))
        return
    log.debug("browser.restore_prompt_cleared", path=str(prefs))


def clear_crash_restore_prompt(user_data_dir: Path) -> None:
    """Mark every profile under *user_data_dir* as having exited cleanly.

    Call this immediately before ``launch_persistent_context``. Chromium reads
    the file on startup, and a user-data-dir normally holds one profile
    (``Default``) though it may hold several -- a prompt from any of them lands
    on the user.

    **Refuses to write a profile something else is holding.** An earlier version
    of this docstring reasoned that "the browser is not running, so the rewrite
    races nothing", which is only true of the browser THIS call is about to
    start. Nothing stopped a second process launching the same persona from
    read-modify-writing ``Preferences`` under a live Chromium -- violating its
    single-writer assumption and able to clobber the running session's state --
    in the window before Playwright's own lock check refused the launch. The
    caller prunes provably-dead locks first, so a lock still present means a
    live or unverifiable owner, and this leaves the file alone.

    Windows gets no protection here: Chromium writes the Singleton trio only on
    POSIX, so there is no lock to consult (see ``profile_lock_present``).
    """
    if not suppress_restore_prompt():
        return
    if profile_lock_present(user_data_dir):
        log.debug("browser.restore_prompt_skipped_profile_in_use", path=str(user_data_dir))
        return
    try:
        candidates = sorted(user_data_dir.glob(f"*/{_PREFERENCES}"))
    except OSError as exc:
        log.debug("browser.restore_prompt_unscannable", path=str(user_data_dir), error=str(exc))
        return
    for prefs in candidates:
        if prefs.is_file():
            _clear_one(prefs)
