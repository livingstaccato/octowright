# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Run the production wheel observer against a deterministic render scheduler.

The VM supplies browser primitives, not a replacement observer. No wall-clock
sleep or browser installation is needed: each frame and wheel is delivered by
the test, and the real promise is inspected after its microtasks have run.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import playwright
import pytest

from octowright.session.emulation_mixin import _SCROLL_SETTLE

_HARNESS = Path(__file__).with_name("_scroll_settle_scheduler.cjs")
_NODE = Path(playwright.__file__).parent / "driver" / ("node.exe" if os.name == "nt" else "node")


@pytest.mark.parametrize(
    "scenario",
    [
        "no_page_deadline",
        "wait_for_wheel",
        "prevented_wheel",
        "boundary_wheel",
        "window_x",
        "window_y",
        "target_x",
        "target_y",
        "ancestor_x",
        "ancestor_y",
        "cancel_before_wheel",
        "cancel_pending_frame",
        "cancel_after_frame",
        "cancel_before_arm",
    ],
)
def test_production_scroll_observer_state_machine(scenario: str) -> None:
    completed = subprocess.run(
        [str(_NODE), str(_HARNESS)],
        input=json.dumps({"source": _SCROLL_SETTLE, "scenario": scenario}),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, f"{scenario}:\n{completed.stdout}\n{completed.stderr}"
