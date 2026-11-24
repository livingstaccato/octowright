# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Standalone exporters retain settings as explicitly inactive metadata."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from octowright.export import export_script


@pytest.mark.parametrize("fmt", ["python", "ts"])
@pytest.mark.parametrize("persistent", [True, False])
def test_export_preserves_flags_as_inactive_comments(tmp_path: Path, fmt: str, persistent: bool) -> None:
    log = tmp_path / "session.jsonl"
    launch = {
        "action": "launch",
        "kind": "chromium",
        "url": "https://example.test",
        "headed": False,
        "show_interactions": True,
        "record_interactions": False,
    }
    if persistent:
        launch["user_data_dir"] = "/tmp/export-profile"
    actions = [launch, {"action": "click", "selector": "button"}]
    log.write_text("\n".join(json.dumps(row) for row in actions))
    # Observations live beside, never inside, the action recording.
    log.with_suffix(".interactions.jsonl").write_text(json.dumps({"type": "interaction", "phase": "down", "x": 99}))
    out = export_script(log, tmp_path / ("out.py" if fmt == "python" else "out.ts"), fmt)
    source = out.read_text()
    prefix = "#" if fmt == "python" else "//"
    assert f"{prefix} Octowright launch settings: show_interactions=true, record_interactions=false" in source
    assert "Standalone Playwright export omits Octowright interaction feedback/capture" in source
    assert "__octowrightInteraction" not in source
    assert "phase" not in source
    executable = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(prefix))
    assert "show_interactions" not in executable and "record_interactions" not in executable
    assert ("await page.click('button')" if fmt == "python" else 'await page.click("button")') in source
    if fmt == "python":
        ast.parse(source)
