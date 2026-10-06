# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The serve-subprocess tests' environment keeps a test daemon off real state.

``serve --daemon-mode`` sweeps every ``octowright-session-*`` directory in the
system temp dir at boot, so a test daemon started with the runner's temp dir
could delete a real daemon's ``session=True`` browser profiles.
"""

from __future__ import annotations

from pathlib import Path

from tests._serve_subprocess import isolated_env


def test_the_temp_dir_is_the_tests_own(tmp_path: Path) -> None:
    env = isolated_env(tmp_path)

    for name in ("TMPDIR", "TEMP", "TMP"):
        assert Path(env[name]).is_relative_to(tmp_path), name
        assert Path(env[name]).is_dir(), name
