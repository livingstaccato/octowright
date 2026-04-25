# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for the browser_open_trace MCP tool — the validation paths only.

The actual `npx playwright show-trace` invocation is left to the integration
boundary; here we monkey-patch subprocess.Popen so the test never spawns a UI.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright import server as _server


@pytest.fixture
def patched_npx(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace `npx` lookup with success and Popen with a stub recording the args."""
    captured: dict[str, Any] = {"calls": []}

    def fake_popen(args: list[str], **kwargs: Any) -> Any:
        captured["calls"].append({"args": list(args), "kwargs": kwargs})
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr("shutil.which", lambda name: "/fake/npx" if name == "npx" else None)
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    return captured


def test_open_trace_with_explicit_path_invokes_npx(patched_npx: dict[str, Any], tmp_path: Path) -> None:
    trace = tmp_path / "trace.zip"
    trace.write_bytes(b"\x00")

    result = _server.browser_open_trace(path=str(trace))

    assert result["path"] == str(trace)
    assert result["pid"] == 4242
    assert patched_npx["calls"] == [
        {
            "args": ["npx", "playwright", "show-trace", str(trace)],
            "kwargs": {
                "stdout": -3,  # subprocess.DEVNULL
                "stderr": -3,
                "start_new_session": True,
            },
        }
    ]


def test_open_trace_missing_file_raises_with_hint(patched_npx: dict[str, Any], tmp_path: Path) -> None:
    """File doesn't exist on disk — better to fail loud than to spawn the viewer on nothing."""
    bogus = tmp_path / "never-recorded.zip"
    with pytest.raises(FileNotFoundError, match="no trace file at"):
        _server.browser_open_trace(path=str(bogus))
    assert patched_npx["calls"] == []


def test_open_trace_without_instance_or_path_is_a_value_error(patched_npx: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="supply either instance_id"):
        _server.browser_open_trace()


def test_open_trace_when_npx_missing_raises_with_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No npx on PATH — error message must point the user at install / fallback command."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    trace = tmp_path / "t.zip"
    trace.write_bytes(b"\x00")
    with pytest.raises(RuntimeError, match="npx not found"):
        _server.browser_open_trace(path=str(trace))


def test_open_trace_with_instance_id_but_no_trace_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """instance_id supplied, but the session was launched without trace=True."""

    class _FakeSession:
        trace_path = None

    fake_pool = SimpleNamespace(get=lambda iid: _FakeSession())
    # Patch the submodule's local binding (where the lookup actually happens),
    # not just the top-level re-export.
    from octowright.server.browser import media as _media

    monkeypatch.setattr(_media, "pool", fake_pool)

    with pytest.raises(RuntimeError, match="not launched with trace=True"):
        _server.browser_open_trace(instance_id="abc")
