# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright restart --help`` must disclose the browser sweep's real scope.

The sweep is ``reap_orphan_browsers(scope="all")`` — a raw PID-level
SIGTERM/SIGKILL over *every* Playwright browser on the machine. It is not
scoped to the daemon's own children, and ``protected`` (a pool/session-level
concept the process reaper never sees) does not survive it. Help text that
says only "orphan browsers" reads as "leftovers from the dead daemon" and
undersells that, so an operator learns the true blast radius by losing a
browser they were watching.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from octowright.cli._root import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _help_text(runner: CliRunner) -> str:
    result = runner.invoke(cli, ["restart", "--help"])
    assert result.exit_code == 0, result.output
    return result.output


def test_restart_help_says_the_sweep_covers_every_browser_on_the_machine(runner: CliRunner) -> None:
    text = _help_text(runner).lower()
    assert "every playwright browser" in text


def test_restart_help_warns_that_protected_does_not_survive_the_sweep(runner: CliRunner) -> None:
    assert "protected" in _help_text(runner).lower()


def test_keep_browsers_flag_help_states_it_is_the_way_to_spare_them(runner: CliRunner) -> None:
    text = _help_text(runner)
    assert "--keep-browsers" in text
    # The flag is the only escape hatch, so its own help must name what it spares
    # rather than repeating the misleading bare word "orphan".
    keep_line_region = text[text.index("--keep-browsers") :]
    assert "protected" in keep_line_region.lower()
