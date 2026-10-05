# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Whether a process command line is an ``octowright serve``.

One definition for every caller that must tell a live daemon from a pid the OS
recycled (``restart``, its split-brain port reclaim, the session-manifest
prune), because a bare ``"octowright serve" in command`` check is right on
POSIX and NEVER matches on Windows: the console script runs as
``...\\octowright.EXE" serve --daemon-mode``, with an extension and a closing
quote between the two words. Found live, where restart called a running
daemon's lockfile pid "not an octowright daemon"; the manifest prune made the
same mistake the other way round and deleted a live daemon's entries.
"""

from __future__ import annotations

import re

# An optional ``.exe``/``.EXE`` and an optional closing quote between the words.
_OCTOWRIGHT_SERVE_RE = re.compile(r"octowright(\.exe)?[\"']?\s+serve", re.IGNORECASE)


def command_names_octowright_serve(command: str) -> bool:
    """True when ``command`` runs ``octowright serve`` on any platform."""
    return _OCTOWRIGHT_SERVE_RE.search(command) is not None
