# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The suite's profiles dir is the throwaway config tree, not the developer's.

``octowright.defaults`` resolves ``PROFILES_DIR`` once, at import. conftest's own
module scope imports ``browser_pool.pool`` (pool leak tracking), which imports
``defaults`` -- and pytest imports conftest BEFORE ``pytest_configure`` runs. So
relocating ``XDG_CONFIG_HOME`` only in that hook came too late: every labelled
launch in the suite created a profile under the real
``~/.config/octowright/profiles`` (found when this branch's live tests left
five there).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

_REAL_CONFIG = Path.home() / ".config" / "octowright"
_REAL_STATE = Path.home() / ".local" / "state" / "octowright"
_TEMP = Path(tempfile.gettempdir()).resolve()


def _throwaway(path: Path) -> bool:
    """Under the temp dir and not the developer's tree.

    Deliberately not "under $XDG_CONFIG_HOME": in a full run the terminal
    plugin's conftest points that at a second temp tree after ``defaults`` has
    already resolved against this one.
    """
    resolved = path.resolve()
    return resolved.is_relative_to(_TEMP) and not resolved.is_relative_to(_REAL_CONFIG.resolve())


def test_profiles_dir_is_a_throwaway_tree() -> None:
    from octowright import defaults, personas

    assert _throwaway(defaults.PROFILES_DIR), defaults.PROFILES_DIR
    assert _throwaway(personas.PROFILES_DIR), personas.PROFILES_DIR


def test_recordings_and_manifest_are_not_the_developers() -> None:
    """Same race, state half: tests that launch through a stub pool without their
    own ``recordings_dir`` wrote JSONL recordings into the real
    ``~/.local/state/octowright/sessions`` (48 files from two runs of one module)."""
    from octowright import defaults, session_manifest

    assert _throwaway(defaults.RECORDINGS_DIR), defaults.RECORDINGS_DIR
    # An autouse fixture already moves the manifest per test; either way it
    # must not be the developer's.
    assert not defaults.SESSION_MANIFEST_PATH.is_relative_to(_REAL_STATE)
    assert not session_manifest.SESSION_MANIFEST_PATH.is_relative_to(_REAL_STATE)
