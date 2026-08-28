# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared fixtures for the macro-artifact suites.

Extracted when tests/test_macro_artifacts_roundtrip.py reached the 777-line
cap exactly. Three suites now drive the same artifact store -- roundtrip,
critical-point normalization, and the pure manifest helpers -- and each needs
the same reload-under-a-tmpdir dance plus a session double.

The reload matters: octowright.defaults, macros.storage and artifacts.paths
all read their roots at import time, so pointing the env vars at a tmp_path is
only half the job. Every module that cached a root has to be reloaded, in
dependency order, or the store under test is the developer's real one.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from tests._operation_gate_fakes import OperationAwareFake


def restore_reloaded_defaults() -> None:
    """Undo the module reloads `_reload` performs.

    Deliberately a plain function rather than an autouse fixture living here:
    an autouse fixture only applies to the module that *defines* it, so each
    suite declares a three-line fixture that calls this. Importing the fixture
    instead works, but reads as an unused import to both ruff and vulture --
    and silencing a dead-code gate to keep a fixture alive is the wrong trade.
    """
    import octowright.artifacts.paths as artifact_paths
    import octowright.defaults as defaults
    import octowright.macros.storage as storage

    importlib.reload(defaults)
    importlib.reload(storage)
    importlib.reload(artifact_paths)


def _reload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "recordings"))
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))

    import octowright.artifacts.paths as artifact_paths
    import octowright.defaults as defaults
    import octowright.macros.artifacts as macro_artifacts
    import octowright.macros.storage as storage

    importlib.reload(defaults)
    importlib.reload(storage)
    importlib.reload(artifact_paths)
    importlib.reload(macro_artifacts)
    return storage, macro_artifacts


def _write_macro(storage, *, name: str = "login") -> Path:
    return storage.write_macro(
        name=name,
        macro={
            "name": name,
            "description": "Login flow",
            "parameters": [],
            "actions": [{"action": "navigate", "url": "https://example.test/login"}],
        },
    )


class _FakeSession(OperationAwareFake):
    """Session double with no page, matching the existing capture=False tests."""

    def __init__(self, tmp_path: Path) -> None:
        self.instance_id = "inst-1"
        super().__init__()
        self.log_path = tmp_path / "recording.jsonl"
        self.log_path.write_text('{"action":"click"}\n', encoding="utf-8")
        self.page = None


class _CapturingSession(_FakeSession):
    """Session that can actually take a screenshot, so ``capture=True`` does work.

    ``_capture_screenshot`` returns early unless ``session.page`` is set AND a
    ``screenshot`` attribute exists, which is why the page-less double above
    cannot exercise the capture path however the flag is set.
    """

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.page = object()
        self.shots: list[Path] = []

    async def screenshot(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")
        self.shots.append(Path(path))


def _passing_critical_point() -> list[dict[str, Any]]:
    """A critical point that passes against a successful run.

    ``run_macro_artifact`` writes ``status: "ok"`` into result.json on success,
    and the ``result_status`` check compares against exactly that.
    """
    return [{"id": "cp1", "checks": [{"type": "result_status", "status": "ok"}]}]


def _stub_replay(monkeypatch: pytest.MonkeyPatch, macro_artifacts) -> None:
    async def fake_run_macro(*, session, name, args, slowmo_ms=None):
        return {"macro": name, "executed": 1, "skipped": 0, "args_used": args or {}, "slowmo_ms": slowmo_ms or 0}

    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", fake_run_macro)
