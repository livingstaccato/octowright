# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that a sequence credential stays a credential under any name.

``{"pin": {"credential": "pin"}}`` resolves to a plain string, and ``pin`` is
not a name the privacy classifier treats as a credential, so before the runner
carried the argument's origin the value was typed, recorded and quoted by the
failure payload in the clear -- and without ``--redact-errors`` that payload is
the JUnit failure text. The macro here fills the value and then fails on an
assertion that reads it back, which is the failure most likely to quote it.
Runs the real CLI in a child interpreter against a real engine, every
octowright path under ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 -- runs this interpreter on fixed arguments
import sys
from pathlib import Path

import pytest

PIN = "pin-4711-planted"  # pragma: allowlist secret
_ENTER_PIN = {
    "name": "enter-pin",
    "description": "type a pin, then fail on an assertion that reads it back",
    "actions": [
        {"action": "evaluate", "expression": "document.body.innerHTML = '<input id=\"code\">'"},
        {"action": "fill", "selector": "#code", "value": "{{pin}}"},
        {"action": "expect_js", "expression": "document.querySelector('#code').value", "equals": "wrong"},
    ],
}
_UNAVAILABLE = ("executable doesn't exist", "missing x server", "cannot open display", "host system is missing")


def _env(tmp_path: Path) -> dict[str, str]:
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
    env["LAB_PIN"] = PIN
    return env


@pytest.mark.live_browser
@pytest.mark.parametrize(
    ("fill_mode", "failed_step"),
    # block (the default): the credential is now held to the fill-origin guard,
    # which refuses typing it into about:blank. warn: the guard lets it through,
    # so the value IS typed and the assertion that fails quotes it.
    [("block", 1), ("warn", 2)],
)
def test_a_sequence_credential_under_an_innocent_name_never_reaches_the_report(
    tmp_path: Path, fill_mode: str, failed_step: int
) -> None:
    pytest.importorskip("playwright")
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "enter-pin.json").write_text(json.dumps(_ENTER_PIN))
    (tmp_path / "profiles" / "lab").mkdir(parents=True)
    (tmp_path / "profiles" / "lab" / "profile.yaml").write_text("name: lab\ncredentials:\n  pin_env: LAB_PIN\n")
    sequence = tmp_path / "pin.json"
    sequence.write_text(json.dumps([{"macro": "enter-pin", "args": {"pin": {"credential": "pin"}}}]))
    artifacts = tmp_path / "recordings" / "evidence"

    proc = subprocess.run(  # nosec B603 -- fixed argv, no shell
        [sys.executable, "-c", "from octowright.cli import main; main()", "test", "--kind", "chromium",
         "--persona", "lab", "--sequence", str(sequence), "--artifacts", str(artifacts)],
        env=dict(_env(tmp_path), OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS=fill_mode), cwd=tmp_path, capture_output=True, text=True, timeout=180, check=False,
    )  # fmt: skip
    if proc.returncode != 1 and any(s in (proc.stdout + proc.stderr).lower() for s in _UNAVAILABLE):
        pytest.skip(f"chromium unavailable here: {proc.stderr[-400:]}")

    report = artifacts / "octowright-report.xml"
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"'failed_at_step': {failed_step}" in report.read_text()  # the failure text was written
    assert PIN not in report.read_text()
    assert PIN not in proc.stdout + proc.stderr
    assert list((tmp_path / "recordings").rglob("*.jsonl")), "the run left no recording to check"
    # The JSONL recording, and any failure HTML dump beside it.
    for written in (tmp_path / "recordings").rglob("*"):
        if written.is_file() and written.suffix in {".jsonl", ".html", ".xml", ".json"}:
            assert PIN not in written.read_text(encoding="utf-8", errors="replace"), written
