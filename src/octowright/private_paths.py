# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Owner-only permissions for directories holding browser credentials.

Two policies live here, deliberately keyed to the two env vars operators
already know rather than a third one: ``OCTOWRIGHT_PROFILES_PRIVATE`` for
persona/profile trees, and ``OCTOWRIGHT_RECORDINGS_PRIVATE`` for the artifact
roots (captures, goldens, macros), which hold the same class of data the JSONL
recording does -- page text, accessibility trees, evaluate results, and
request headers.

A persona profile directory is the strongest credential store octowright
writes: it holds live session cookies, ``localStorage``, and IndexedDB for
every site the persona has logged into. Chromium hardens its own profile
root, but Firefox and WebKit do not -- their ``cookies.sqlite`` lands at
``0644`` inside a ``0755`` tree, so on a shared host any other local user can
copy a logged-in session straight off disk.

That is a strictly stronger capability than the typed password the recorder
already protects at ``0600``, so the two controls now match. Locking the
directory to ``0700`` denies traversal, which covers every file the browser
creates inside it without octowright having to chase per-file modes the
engine owns.

Best-effort by design: a ``chmod`` that fails (exotic filesystem, read-only
mount, foreign owner) must never block a launch.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from provide.telemetry import get_logger

log = get_logger(__name__)

# Shared with the recorder's OCTOWRIGHT_RECORDINGS_PRIVATE parsing.
PRIVATE_OFF = frozenset({"0", "off", "false", "no", "never", "none", "disabled"})

PROFILES_PRIVATE_ENV = "OCTOWRIGHT_PROFILES_PRIVATE"
RECORDINGS_PRIVATE_ENV = "OCTOWRIGHT_RECORDINGS_PRIVATE"


def profiles_private() -> bool:
    """Whether to lock persona/profile directories to the owner (0700).

    Default ON. Opt out with ``OCTOWRIGHT_PROFILES_PRIVATE`` set to a falsey
    token for setups that intentionally share profiles between local users.
    """
    return os.environ.get(PROFILES_PRIVATE_ENV, "on").strip().lower() not in PRIVATE_OFF


def recordings_private() -> bool:
    """Whether to lock recording-adjacent artifact directories to the owner (0700).

    Default ON, and an EMPTY value still means on -- matching the recorder's
    own parsing of the same variable, so only an explicit falsey token widens
    a security default.
    """
    return os.environ.get(RECORDINGS_PRIVATE_ENV, "on").strip().lower() not in PRIVATE_OFF


def _chmod_owner_only(path: Path) -> None:
    """Best-effort ``chmod 0700``. A failure must never break the caller."""
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)


def secure_directory(path: Path) -> None:
    """Best-effort ``chmod 0700`` on *path* when the profile policy is on."""
    if not profiles_private():
        return
    _chmod_owner_only(path)


def _lock_tree(leaf: Path, root: Path, label: str) -> None:
    """Lock *leaf* and every directory up to and including *root*.

    The walk is bounded by an explicit containment check first: without it a
    leaf that is not under *root* would walk to the filesystem root, chmod-ing
    unrelated directories to 0700 on the way. A non-contained leaf is locked on
    its own and the walk stops.
    """
    _chmod_owner_only(leaf)
    try:
        leaf.relative_to(root)
    except ValueError:
        log.debug(f"octowright.{label}.outside_root", leaf=str(leaf), root=str(root))
        return
    current = leaf
    while current != root:
        current = current.parent
        _chmod_owner_only(current)


def secure_artifact_tree(leaf: Path, root: Path) -> None:
    """Lock a captures/goldens/macros directory tree to the owner (0700).

    The DIRECTORY is the control rather than the file mode, and not as a
    stylistic preference: ``atomic_write_text`` deliberately preserves an
    existing target's mode, because an atomic write must be a content
    replacement and not a silent permission change. A file first written at
    0644 before that helper existed therefore keeps 0644 through every later
    rewrite, forever. A 0700 directory denies traversal and so covers every
    file inside regardless of age or mode -- the same argument the profile
    tree already makes.

    Captures nest as ``root/host/session``, so an unreadable leaf under a
    world-readable parent would still leak the host names and session ids the
    paths are built from. Goldens and macros are flat, and pass the root as
    both arguments.
    """
    if not recordings_private():
        return
    _lock_tree(leaf, root, "artifacts")


def secure_profile_tree(leaf: Path, root: Path) -> None:
    """Lock *leaf* and every directory up to and including *root*.

    The per-engine leaf is what holds the cookies, but an unreadable leaf
    inside a world-readable parent still leaks the persona names, so the walk
    continues up to the profiles root.

    The walk is bounded by an explicit containment check first: without it a
    leaf that is *not* under *root* (an operator pointing
    ``OCTOWRIGHT_PROFILES_DIR`` somewhere unexpected) would walk to the
    filesystem root, chmod-ing unrelated directories to 0700 on the way. A
    non-contained leaf gets locked on its own and the walk stops.
    """
    if not profiles_private():
        return
    _lock_tree(leaf, root, "profiles")
