# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Run the real injected asset under a deterministic JS DOM/scheduler."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "src/octowright/browser_pool/_assets/interactions.js"


@pytest.mark.parametrize(
    "case",
    [
        "hold",
        "buttons_contacts",
        "double",
        "drag_wheel",
        "cleanup",
        "modes_repeat",
        "frame_relay",
        "late_replies",
        "reused_contact",
        "simultaneous_touch_not_double",
        "malformed",
    ],
)
def test_interaction_observer(case: str) -> None:
    node = shutil.which("node")
    assert node, "Node is required to exercise the injected interaction observer"
    result = subprocess.run(
        [node, str(ROOT / "tests/js/interaction_observer.cjs"), str(ASSET), case],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
