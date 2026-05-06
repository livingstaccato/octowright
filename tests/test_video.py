# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright.video_overlay import render_overlay_image

# ---------------------------------------------------------------------------
# Helpers — reload video module with patched shutil.which
# ---------------------------------------------------------------------------


def _import_video(monkeypatch: pytest.MonkeyPatch, ffmpeg_path: str | None = "/fake/ffmpeg"):
    """Reload octowright.video with shutil.which patched."""
    import octowright.video as _v

    monkeypatch.setattr("octowright.video.shutil.which", lambda name: ffmpeg_path if name == "ffmpeg" else None)
    importlib.reload(_v)
    # Re-apply patch after reload so tests pick up the new module object
    monkeypatch.setattr(
        _v,
        "ensure_ffmpeg",
        lambda: (
            ffmpeg_path
            or (_ for _ in ()).throw(
                RuntimeError("ffmpeg not found on PATH — install it first (e.g. 'brew install ffmpeg')")
            )
        ),
    )
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


def _read_ppm_pixel(path: Path, x: int, y: int) -> tuple[int, int, int]:
    with path.open("rb") as handle:
        magic = handle.readline().strip()
        if magic != b"P6":
            raise AssertionError(f"unexpected ppm header: {magic!r}")
        width, _height = (int(part) for part in handle.readline().split())
        max_value = handle.readline().strip()
        if max_value != b"255":
            raise AssertionError(f"unexpected ppm max value: {max_value!r}")
        payload = handle.read()
    offset = ((y * width) + x) * 3
    return tuple(payload[offset : offset + 3])  # type: ignore[return-value]


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


def test_extract_frames_raises_when_neither_fps_nor_at_times(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import octowright.video as _v

    monkeypatch.setattr("octowright.video.shutil.which", lambda name: "/fake/ffmpeg")
    with pytest.raises(ValueError, match="exactly one"):
        _v.extract_frames(tmp_path / "vid.webm", tmp_path / "out")


def test_extract_frames_raises_when_both_fps_and_at_times(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import octowright.video as _v

    monkeypatch.setattr("octowright.video.shutil.which", lambda name: "/fake/ffmpeg")
    with pytest.raises(ValueError, match="exactly one"):
        _v.extract_frames(tmp_path / "vid.webm", tmp_path / "out", fps=2.0, at_times=[0.5])


# ---------------------------------------------------------------------------
# extract_frames — fps mode command line
# ---------------------------------------------------------------------------


def test_extract_frames_fps_command_includes_fps_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    for i, (t, cmd) in enumerate(zip(times, cmds, strict=True)):
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


def test_extract_frames_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_extract_frame_emits_single_frame_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _v, cmds = _import_video_mock_run(monkeypatch, tmp_path)
    video = tmp_path / "test.webm"
    video.touch()
    out = tmp_path / "poster.png"

    _v.extract_frame(video, out, at_time=1.25)

    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd[0] == "/fake/ffmpeg"
    assert cmd[cmd.index("-ss") + 1] == "1.25"
    assert cmd[cmd.index("-i") + 1] == str(video)
    assert cmd[-2] == str(out)


def test_compose_video_grid_builds_xstack_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _v, cmds = _import_video_mock_run(monkeypatch, tmp_path)
    inputs = [tmp_path / "a.webm", tmp_path / "b.webm", tmp_path / "c.webm"]
    for path in inputs:
        path.touch()
    out = tmp_path / "composite.mp4"

    _v.compose_video_grid(inputs, out, columns=3, cell_width=640, cell_height=360)

    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd[0] == "/fake/ffmpeg"
    assert cmd.count("-i") == 3
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "xstack=inputs=3" in filter_complex
    assert "layout=0_0|640_0|1280_0" in filter_complex
    assert "scale=640:360" in filter_complex
    assert cmd[-2] == str(out)


def test_compose_video_layout_builds_custom_positions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _v, cmds = _import_video_mock_run(monkeypatch, tmp_path)
    sources = [tmp_path / "a.webm", tmp_path / "b.webm"]
    for path in sources:
        path.touch()
    out = tmp_path / "featured.mp4"

    _v.compose_video_layout(
        [
            {"source": sources[0], "x": 0, "y": 0, "width": 1280, "height": 720},
            {"source": sources[1], "x": 1280, "y": 0, "width": 640, "height": 360},
        ],
        out,
    )

    assert len(cmds) == 1
    filter_complex = cmds[0][cmds[0].index("-filter_complex") + 1]
    assert "xstack=inputs=2" in filter_complex
    assert "layout=0_0|1280_0" in filter_complex
    assert "scale=1280:720" in filter_complex
    assert "scale=640:360" in filter_complex


def test_apply_video_overlay_builds_overlay_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _v, cmds = _import_video_mock_run(monkeypatch, tmp_path)
    source = tmp_path / "base.mp4"
    source.touch()
    out = tmp_path / "overlay.mp4"

    _v.apply_video_overlay(
        source,
        out,
        title="Seven Mix Orchestration",
        subtitle="seven-mix-orchestration | flagship hero",
        panes=[
            {"persona": "p1", "role": "player", "kind": "chromium", "x": 0, "y": 0},
            {"persona": "ops", "role": "monitor", "kind": "webkit", "x": 960, "y": 720},
        ],
        canvas_width=1920,
        canvas_height=1080,
    )

    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd.count("-i") == 2
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert filter_complex == "[1:v]colorkey=0xFF00FF:0.01:0.0[ol];[0:v][ol]overlay=0:0[v]"
    assert cmd[-2] == str(out)


def test_probe_video_reads_dimensions_and_duration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import octowright.video as _v

    monkeypatch.setattr("octowright.video.shutil.which", lambda name: "/fake/ffprobe" if name == "ffprobe" else None)

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = '{"streams":[{"width":1920,"height":1080}],"format":{"duration":"3.5"}}'
        return result

    monkeypatch.setattr("octowright.video.subprocess.run", fake_run)

    metadata = _v.probe_video(tmp_path / "demo.mp4")

    assert metadata == {"width": 1920, "height": 1080, "duration_seconds": 3.5}


def test_render_overlay_image_uses_translucent_safe_area_defaults(tmp_path: Path) -> None:
    path = render_overlay_image(
        tmp_path / "overlay.ppm",
        title="Alpha",
        subtitle="Quiet metadata",
        panes=[],
        canvas_width=1920,
        canvas_height=1080,
    )

    assert path.exists()
    assert _read_ppm_pixel(path, 40, 40) == (255, 0, 255)
    assert _read_ppm_pixel(path, 60, 980) != (255, 0, 255)
