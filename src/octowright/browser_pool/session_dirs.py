# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``session=True`` profile-dir naming + orphan reaping.

A ``session=True`` launch mints a tmpdir profile that lives for the daemon's
lifetime (see ``BrowserPool._resolve_session_dir``). ``shutdown_pool`` removes
them on clean exit, but a SIGKILL'd daemon orphans them under the system temp
dir, where they accumulate forever.

The singleton leader election guarantees exactly one daemon owns these at a
time, so a freshly-elected leader can safely reap every survivor at startup
(the reaper is *not* run in ``--no-singleton`` mode, where a sibling daemon may
own live dirs). Kept free of the heavy ``pool`` import so it stays pure and
cheap to test.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

# Shared by pool._resolve_session_dir (mint) and the reaper (sweep) so the
# naming contract lives in exactly one place.
SESSION_TMPDIR_PREFIX = "octowright-session-"


def reap_stale_session_dirs(
    temp_dir: Path | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove leftover ``octowright-session-*`` dirs under ``temp_dir``.

    Defaults to the system temp dir. Skips anything that isn't a directory.
    A failed ``rmtree`` is recorded in ``errors`` and does not abort the sweep.

    Returns ``{"removed": [str], "errors": [{"path", "error"}], "dry_run"}``.
    """
    base = temp_dir if temp_dir is not None else Path(tempfile.gettempdir())
    removed: list[str] = []
    errors: list[dict[str, str]] = []

    try:
        candidates = sorted(base.glob(f"{SESSION_TMPDIR_PREFIX}*"))
    except OSError:
        # base missing or unreadable — nothing to reap.
        return {"removed": removed, "errors": errors, "dry_run": dry_run}

    for path in candidates:
        if not path.is_dir():
            continue
        if dry_run:
            removed.append(str(path))
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        removed.append(str(path))

    return {"removed": removed, "errors": errors, "dry_run": dry_run}
