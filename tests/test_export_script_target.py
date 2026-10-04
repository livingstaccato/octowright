# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``browser_export_script(out_path=...)`` may only write a script.

Containment alone let it replace ANY file under the recordings root with
generated source: another session's ``.jsonl`` recording, or a macro
artifact's ``artifact.json`` -- the person-authored critical points that
``octowright cleanup`` deliberately never touches.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from octowright.server.browser import inspect as _inspect


@pytest.fixture
def recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "recordings"
    root.mkdir()
    log = root / "20260101T000000Z-chromium-abc123.jsonl"
    log.write_text(json.dumps({"action": "navigate", "url": "https://example.test/"}) + "\n")
    pool = MagicMock()
    pool.get.return_value = SimpleNamespace(log_path=log)
    monkeypatch.setattr(_inspect, "pool", pool)
    monkeypatch.setattr(_inspect, "RECORDINGS_DIR", root)
    return root


@pytest.mark.parametrize(
    ("relative", "why"),
    [
        ("20260101T000000Z-chromium-other1.jsonl", "another session's recording"),
        ("artifacts/macros/login/artifact.json", "a macro artifact manifest"),
        ("artifacts/macros/login/replay.py", "anything under artifacts/"),
        ("session-artifacts/abc123/x.py", "a plugin's committed session artifact"),
        ("out.txt", "not a script suffix"),
        ("out.ts", "the wrong suffix for format=python"),
    ],
)
def test_export_refuses_a_target_that_is_not_a_script_it_owns(recordings: Path, relative: str, why: str) -> None:
    target = recordings / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("KEEP")

    with pytest.raises(ValueError, match="export_script out_path"):
        _inspect.browser_export_script("i", format="python", out_path=str(target))

    assert target.read_text() == "KEEP", why


def test_export_still_writes_and_rewrites_a_script(recordings: Path) -> None:
    target = recordings / "exports" / "replay.py"
    target.parent.mkdir()

    first = _inspect.browser_export_script("i", format="python", out_path=str(target))
    again = _inspect.browser_export_script("i", format="python", out_path=str(target))
    default = _inspect.browser_export_script("i", format="ts")

    assert Path(first["path"]) == Path(again["path"]) == target.resolve()
    assert "example.test" in target.read_text()
    assert default["path"].endswith(".ts")
