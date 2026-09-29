# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof of ``octowright test --sequence ... --record-video``.

Runs the real CLI in a child interpreter against a real engine, with every
octowright path pointed under ``tmp_path``. The sequence FAILS on its second
step, because a failed run's video is the one an operator actually wants: the
test asserts the run exits 1 and still leaves a complete, non-empty WebM under
the stable name, and prints its path after the report line. It runs as a persona, the way the lab does, so the
browser is a persistent context.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 -- runs this interpreter on fixed arguments
import sys
from pathlib import Path

import pytest

_PAINT = {
    "name": "paint",
    "description": "paint something worth recording",
    "actions": [
        {
            "action": "evaluate",
            "expression": "document.body.innerHTML = '<h1 style=\"font-size:80px\">recorded</h1>'",
        },
        {"action": "evaluate", "expression": "new Promise(r => setTimeout(r, 800))"},
    ],
}
_BREAK = {
    "name": "break",
    "description": "fail on purpose",
    "actions": [{"action": "evaluate", "expression": "(() => { throw new Error('boom') })()"}],
}
_UNAVAILABLE = ("executable doesn't exist", "missing x server", "cannot open display", "host system is missing")


def _env(tmp_path: Path) -> dict[str, str]:
    # XDG state/config too: a chromium launch writes its new-tab extension under
    # the user state dir, which is the running daemon's otherwise. The cache dir
    # is left alone -- Playwright finds its browsers there.
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path / "xdg-state"), XDG_CONFIG_HOME=str(tmp_path / "xdg-config"))
    for key, rel in {
        "OCTOWRIGHT_RECORDINGS": "recordings",
        "OCTOWRIGHT_PROFILES_DIR": "profiles",
        "OCTOWRIGHT_MACROS_DIR": "macros",
        "OCTOWRIGHT_SCENARIOS_DIR": "scenarios",
        "OCTOWRIGHT_CAPTURES_DIR": "captures",
        "OCTOWRIGHT_GOLDENS_DIR": "goldens",
        "OCTOWRIGHT_UPLOAD_STAGING_DIR": "uploads",
        "OCTOWRIGHT_SESSION_MANIFEST": "state/session-manifest.json",
        "OCTOWRIGHT_ADVISOR_STATE": "state/advisor.json",
        "OCTOWRIGHT_LOCK_PATH": "state/octowright.lock",
        "OCTOWRIGHT_BRIDGE_STATE": "state/bridge-state.json",
        "OCTOWRIGHT_UPGRADE_STATE": "state/upgrade.json",
    }.items():
        env[key] = str(tmp_path / rel)
    return env


@pytest.mark.live_browser
@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
def test_a_failed_sequence_leaves_a_real_video(tmp_path: Path, kind: str) -> None:
    pytest.importorskip("playwright")
    macros = tmp_path / "macros"
    macros.mkdir()
    for macro in (_PAINT, _BREAK):
        (macros / f"{macro['name']}.json").write_text(json.dumps(macro))
    sequence = tmp_path / "repair.json"
    sequence.write_text(json.dumps([{"macro": "paint"}, {"macro": "break"}]))
    artifacts = tmp_path / "recordings" / "evidence"
    # A persona, as the lab runs it: its browser is a persistent context.
    (tmp_path / "profiles" / "lab").mkdir(parents=True)
    (tmp_path / "profiles" / "lab" / "profile.yaml").write_text("name: lab\n")

    proc = subprocess.run(  # nosec B603 -- fixed argv, no shell
        [sys.executable, "-c", "from octowright.cli import main; main()", "test", "--kind", kind,
         "--persona", "lab", "--sequence", str(sequence), "--artifacts", str(artifacts), "--redact-errors", "--record-video"],
        env=_env(tmp_path), cwd=tmp_path, capture_output=True, text=True, timeout=180, check=False,
    )  # fmt: skip
    if proc.returncode != 1 and any(s in (proc.stdout + proc.stderr).lower() for s in _UNAVAILABLE):
        pytest.skip(f"{kind} unavailable here: {proc.stderr[-400:]}")

    video = (artifacts / "repair.webm").resolve()
    assert proc.returncode == 1, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[-2:] == [f"report: {(artifacts / 'octowright-report.xml').resolve()}", f"video: {video}"], lines
    assert video.stat().st_size > 0
    assert video.read_bytes()[:4] == bytes.fromhex("1a45dfa3")  # EBML: a complete WebM header
