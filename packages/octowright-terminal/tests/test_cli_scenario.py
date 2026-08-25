# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""`octowright scenario start` can run a terminal participant.

The regression this guards was total: the extraction removed the CLI's
terminal-pool wiring and put nothing in its place, so `scenario start` refused
*every* plugin-kind participant with an unhandled traceback saying to enable
`OCTOWRIGHT_PLUGINS` — which the operator had already done. Only `selftest`
and `serve` import `octowright.server`, and that import is what populates the
plugin registry `_validate_participant_kind` reads.

A subprocess, not `CliRunner`: plugin activation is an *import* side effect
keyed on `OCTOWRIGHT_PLUGINS`, so it cannot be re-run under a different
environment inside a process that has already imported `octowright.server`.
Testing it any other way tests something that is not the bug. Core's
`tests/test_cli_scenario_branches.py::TestScenarioStartPluginActivation` pins
the wiring cheaply; this pins that the wiring actually launches a real PTY.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")

#: Run the CLI through the interpreter running these tests rather than a
#: console script on PATH, so the subprocess is guaranteed to import the
#: same checkout the suite is testing.
_CLI_MAIN = "from octowright.cli import main; main()"

_SCENARIO = textwrap.dedent(
    """
    name: term-only
    participants:
      - persona: tanuki-tim
        role: player
        kind: terminal
        options:
          connector_type: pty
          command: /bin/cat
    """
).strip()


def _run_scenario_start(
    scenarios_dir: Path, recordings_dir: Path, *, plugins: str | None
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "OCTOWRIGHT_SCENARIOS_DIR": str(scenarios_dir),
        "OCTOWRIGHT_RECORDINGS": str(recordings_dir),
    }
    env.pop("OCTOWRIGHT_PLUGINS", None)
    if plugins is not None:
        env["OCTOWRIGHT_PLUGINS"] = plugins
    return subprocess.run(
        [sys.executable, "-c", _CLI_MAIN, "scenario", "start", "term-only", "--test"],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        check=False,
    )


@pytest.fixture
def scenarios_dir(tmp_path: Path) -> Path:
    target = tmp_path / "scenarios"
    target.mkdir()
    (target / "term-only.yaml").write_text(_SCENARIO + "\n", encoding="utf-8")
    return target


def test_scenario_start_launches_a_terminal_participant(scenarios_dir: Path, tmp_path: Path) -> None:
    """With the plugin enabled, the participant launches and gets an instance id.

    `--test` on a scenario with no verify macros exits 2 after start+stop, which
    is the cheapest way to drive the full start→teardown path without needing a
    macro terminal cannot run anyway.
    """
    result = _run_scenario_start(scenarios_dir, tmp_path / "rec", plugins="terminal")
    combined = result.stdout + result.stderr

    assert "unsupported kind" not in combined, combined
    assert "Traceback" not in combined, combined
    assert "scenario_id:" in combined, combined
    # The participant line: role, persona, kind, then a real 12-hex instance id.
    participant = next((ln for ln in combined.splitlines() if "terminal" in ln and "tanuki-tim" in ln), None)
    assert participant is not None, combined
    assert participant.split()[-1].strip(), participant


def test_scenario_start_still_refuses_the_kind_when_the_plugin_is_not_enabled(
    scenarios_dir: Path, tmp_path: Path
) -> None:
    """Activation must not change what loads by default.

    The whole point of `OCTOWRIGHT_PLUGINS` is that installing a distribution
    only makes it discoverable; a CLI that activated everything installed would
    hand a browser-driving process new capability with no operator decision.
    """
    result = _run_scenario_start(scenarios_dir, tmp_path / "rec", plugins=None)
    combined = result.stdout + result.stderr

    assert result.returncode != 0
    assert "unsupported kind 'terminal'" in combined, combined
    assert "OCTOWRIGHT_PLUGINS" in combined, combined
