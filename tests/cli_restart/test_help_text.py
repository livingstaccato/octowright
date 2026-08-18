# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright restart``'s browser sweep must match what it says it does.

The sweep used to be ``reap_orphan_browsers(scope="all")`` — a raw PID-level
SIGTERM/SIGKILL over *every* Playwright browser on the machine — while the
command printed ``orphan browsers: killed=N``. `protected` is a pool/session
concept the process reaper never sees, and headed browsers are protected BY
DEFAULT, so following the documented transport-recovery procedure could SIGKILL
the windows the user was actively watching, plus any unrelated Playwright run.

0.14.4 answered that by documenting the blast radius. That was the wrong half
of the fix, and the prose it added was itself wrong twice: it pointed at a
`close_strays` symbol that does not exist anywhere in the repo, and claimed
`cleanup` spares protected browsers when ``cli/cleanup.py`` makes the identical
``scope="all"`` call. The sweep runs AFTER ``_stop_leader``, at which point the
dead daemon's browsers are exactly what ``scope="orphaned"`` selects — the same
scope the boot and housekeeping sweeps already use.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from octowright.cli import restart as restart_mod
from octowright.cli._root import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _help_text(runner: CliRunner) -> str:
    result = runner.invoke(cli, ["restart", "--help"])
    assert result.exit_code == 0, result.output
    return result.output


def test_sweep_targets_only_browsers_the_dead_daemon_orphaned(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scope, not just the help text — this is the finding's real half."""
    seen: dict[str, object] = {}

    def _fake_reap(scope: str, **kwargs: object) -> dict[str, list[int]]:
        seen["scope"] = scope
        return {"killed": [], "still_alive": [], "errors": []}

    monkeypatch.setattr(restart_mod, "reap_orphan_browsers", _fake_reap)
    restart_mod._reap_browsers()
    assert seen["scope"] == "orphaned"


def test_help_does_not_reference_a_symbol_that_does_not_exist(runner: CliRunner) -> None:
    assert "close_strays" not in _help_text(runner)


def test_source_does_not_reference_a_symbol_that_does_not_exist() -> None:
    """`close_strays` appeared exactly once in the whole repo: in this docstring."""
    source = Path(restart_mod.__file__).read_text(encoding="utf-8")
    assert "close_strays" not in source


def test_help_does_not_claim_cleanup_spares_protected_browsers(runner: CliRunner) -> None:
    """`cli/cleanup.py` makes the identical scope="all" call and never reads
    `protected`, so pointing an operator at it as the safe alternative is how
    they lose the same windows twice."""
    text = _help_text(runner).lower()
    assert "protected only holds against cleanup" not in text


def test_keep_browsers_flag_is_still_documented_as_the_opt_out(runner: CliRunner) -> None:
    text = _help_text(runner)
    assert "--keep-browsers" in text
