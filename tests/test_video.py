from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — reload video module with patched shutil.which
# ---------------------------------------------------------------------------


def _import_video(monkeypatch: pytest.MonkeyPatch, ffmpeg_path: str | None = "/fake/ffmpeg"):
    """Reload octowright.video with shutil.which patched."""
    import octowright.video as _v
    monkeypatch.setattr("octowright.video.shutil.which", lambda name: ffmpeg_path if name == "ffmpeg" else None)
    importlib.reload(_v)
    # Re-apply patch after reload so tests pick up the new module object
    monkeypatch.setattr(_v, "ensure_ffmpeg", lambda: ffmpeg_path or (_ for _ in ()).throw(
        RuntimeError("ffmpeg not found on PATH — install it first (e.g. 'brew install ffmpeg')")
    ))
    return _v


def _import_video_mock_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Return video module with ffmpeg found and subprocess.run mocked to succeed."""
    import octowright.video as _v
    importlib.reload(_v)
    monkeypatch.setattr("octowright.video.shutil.which", lambda name: "/fake/ffmpeg" if name == "ffmpeg" else None)

    captured_cmds: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured_cmds.append(cmd)
        # Simulate ffmpeg creating output files for fps mode
        for arg in cmd:
            if arg.endswith(".png"):
                Path(arg).parent.mkdir(parents=True, exist_ok=True)
                Path(arg).touch()
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    monkeypatch.setattr("octowright.video.subprocess.run", fake_run)
    return _v, captured_cmds


# ---------------------------------------------------------------------------
# ensure_ffmpeg
# ---------------------------------------------------------------------------


def test_ensure_ffmpeg_returns_path_when_found(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.video as _v
    monkeypatch.setattr("octowright.video.shutil.which", lambda name: "/usr/local/bin/ffmpeg")
    assert _v.ensure_ffmpeg() == "/usr/local/bin/ffmpeg"


def test_ensure_ffmpeg_raises_with_brew_hint_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.video as _v
    monkeypatch.setattr("octowright.video.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="brew install ffmpeg"):
        _v.ensure_ffmpeg()


# ---------------------------------------------------------------------------
# extract_frames — argument validation
# ---------------------------------------------------------------------------


def test_extract_frames_raises_when_neither_fps_nor_at_times(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import octowright.video as _v
    monkeypatch.setattr("octowright.video.shutil.which", lambda name: "/fake/ffmpeg")
    with pytest.raises(ValueError, match="exactly one"):
        _v.extract_frames(tmp_path / "vid.webm", tmp_path / "out")


def test_extract_frames_raises_when_both_fps_and_at_times(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import octowright.video as _v
    monkeypatch.setattr("octowright.video.shutil.which", lambda name: "/fake/ffmpeg")
    with pytest.raises(ValueError, match="exactly one"):
        _v.extract_frames(tmp_path / "vid.webm", tmp_path / "out", fps=2.0, at_times=[0.5])


# ---------------------------------------------------------------------------
# extract_frames — fps mode command line
# ---------------------------------------------------------------------------


def test_extract_frames_fps_command_includes_fps_filter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _v, cmds = _import_video_mock_run(monkeypatch, tmp_path)
    video = tmp_path / "test.webm"
    video.touch()
    out = tmp_path / "frames"

    _v.extract_frames(video, out, fps=2.0)

    assert len(cmds) == 1
    cmd = cmds[0]
    assert "/fake/ffmpeg" in cmd[0]
    # Must include -vf fps=... filter
    vf_idx = cmd.index("-vf")
    assert "fps=2.0" in cmd[vf_idx + 1]
    # Must point -i at our video
    i_idx = cmd.index("-i")
    assert str(video) in cmd[i_idx + 1]


# ---------------------------------------------------------------------------
# extract_frames — at_times mode command line
# ---------------------------------------------------------------------------


def test_extract_frames_at_times_issues_one_command_per_timestamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _v, cmds = _import_video_mock_run(monkeypatch, tmp_path)
    video = tmp_path / "test.webm"
    video.touch()
    out = tmp_path / "frames"

    times = [0.5, 1.0, 2.5]
    _v.extract_frames(video, out, at_times=times)

    assert len(cmds) == len(times)
    for i, (t, cmd) in enumerate(zip(times, cmds)):
        # -ss <timestamp> must appear
        ss_idx = cmd.index("-ss")
        assert str(t) in cmd[ss_idx + 1]
        # Output filename must encode index and time
        out_file = cmd[-2]  # second-to-last arg before -y
        assert f"{i:03d}" in out_file
        assert f"{t:.3f}" in out_file


# ---------------------------------------------------------------------------
# extract_frames — non-zero exit code raises RuntimeError
# ---------------------------------------------------------------------------


def test_extract_frames_raises_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import octowright.video as _v
    monkeypatch.setattr("octowright.video.shutil.which", lambda name: "/fake/ffmpeg")

    def bad_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        result = MagicMock()
        result.returncode = 1
        result.stderr = "some ffmpeg error"
        return result

    monkeypatch.setattr("octowright.video.subprocess.run", bad_run)

    video = tmp_path / "vid.webm"
    video.touch()
    with pytest.raises(RuntimeError, match="ffmpeg exited 1"):
        _v.extract_frames(video, tmp_path / "out", fps=1.0)
