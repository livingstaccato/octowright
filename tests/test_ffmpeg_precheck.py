# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Regression tests for the ffmpeg precheck inside extract_frames.

If `ffmpeg` is missing on PATH, callers must get a clear, actionable
RuntimeError that names install commands for both macOS and Linux,
not a confusing FileNotFoundError from the subprocess invocation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import octowright.video as video_mod


def test_extract_frames_raises_runtime_error_when_ffmpeg_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With ffmpeg absent, extract_frames must raise RuntimeError, not FileNotFoundError."""
    monkeypatch.setattr("octowright.video.shutil.which", lambda name: None)

    video = tmp_path / "vid.webm"
    video.touch()
    out = tmp_path / "frames"

    with pytest.raises(RuntimeError) as excinfo:
        video_mod.extract_frames(video, out, fps=1.0)

    msg = str(excinfo.value)
    assert "ffmpeg not found" in msg
    assert "brew install" in msg
    assert "apt-get install" in msg


def test_extract_frames_proceeds_when_ffmpeg_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Precheck must not block normal operation: with ffmpeg on PATH and subprocess
    stubbed, extract_frames runs through and returns its frame list."""
    monkeypatch.setattr("octowright.video.shutil.which", lambda name: "/usr/local/bin/ffmpeg")

    captured: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured.append(cmd)
        # Materialise the output png so the post-extract glob picks it up.
        for arg in cmd:
            if arg.endswith(".png"):
                Path(arg).parent.mkdir(parents=True, exist_ok=True)
                Path(arg).touch()
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    monkeypatch.setattr("octowright.video.subprocess.run", fake_run)

    video = tmp_path / "vid.webm"
    video.touch()
    out = tmp_path / "frames"

    frames = video_mod.extract_frames(video, out, fps=2.0)

    assert len(captured) == 1
    assert "/usr/local/bin/ffmpeg" in captured[0][0]
    # extract_frames returned the produced frame paths.
    assert all(p.suffix == ".png" for p in frames)
