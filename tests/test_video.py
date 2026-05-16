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

from octowright.video_overlay import DEFAULT_OVERLAY_BOX, TRANSPARENT, render_overlay_image

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


def _read_png_pixel(path: Path, x: int, y: int) -> tuple[int, int, int, int]:
    """Decode one RGBA pixel from a PNG written by video_overlay._write_png.

    Pure stdlib (struct + zlib). Assumes 8-bit RGBA, no interlacing, filter
    type 0 on every scanline — the constraints _write_png itself enforces.
    """
    import struct
    import zlib

    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"not a PNG: {data[:8]!r}")
    pos = 8
    width = 0
    idat_chunks: list[bytes] = []
    while pos < len(data):
        (chunk_len,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + chunk_len]
        pos += 8 + chunk_len + 4
        if tag == b"IHDR":
            width, _height, _bd, _ct, *_ = struct.unpack(">IIBBBBB", chunk)
        elif tag == b"IDAT":
            idat_chunks.append(chunk)
        elif tag == b"IEND":
            break
    decompressed = zlib.decompress(b"".join(idat_chunks))
    stride = width * 4 + 1  # +1 for the filter byte at the start of each row
    row_start = y * stride + 1  # skip the filter byte
    offset = row_start + x * 4
    return tuple(decompressed[offset : offset + 4])  # type: ignore[return-value]


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


def test_render_supporting_video_writes_video_and_poster(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import octowright.video as _v

    source = tmp_path / "source.webm"
    source.write_bytes(b"source")
    target = tmp_path / "supporting" / "player.mp4"
    poster = tmp_path / "supporting" / "player.png"
    calls: list[tuple[str, Path, Path]] = []

    def _fake_transcode(src: Path, dst: Path) -> Path:
        calls.append(("video", Path(src), Path(dst)))
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"video")
        return Path(dst)

    def _fake_extract(src: Path, dst: Path, *, at_time: float = 0.5) -> Path:
        assert at_time == 1.75
        calls.append(("poster", Path(src), Path(dst)))
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"poster")
        return Path(dst)

    monkeypatch.setattr("octowright.video.transcode_video", _fake_transcode)
    monkeypatch.setattr("octowright.video.extract_frame", _fake_extract)
    monkeypatch.setattr("octowright.video.optimize_png", lambda path, **kwargs: path)
    monkeypatch.setattr("octowright.video.poster_capture_time", lambda path: 1.75)

    result = _v.render_supporting_video(source, target, poster_path=poster)

    assert result == {"path": str(target), "poster_path": str(poster)}
    assert calls == [
        ("video", source, target),
        ("poster", target, poster),
    ]


def test_poster_capture_time_targets_mid_clip_but_caps_at_two_seconds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import octowright.video as _v

    monkeypatch.setattr("octowright.video.probe_video", lambda path: {"duration_seconds": 8.0})
    assert _v.poster_capture_time(tmp_path / "long.mp4") == 2.0

    monkeypatch.setattr("octowright.video.probe_video", lambda path: {"duration_seconds": 3.0})
    assert _v.poster_capture_time(tmp_path / "medium.mp4") == 1.5

    monkeypatch.setattr("octowright.video.probe_video", lambda path: {"duration_seconds": 0.4})
    assert _v.poster_capture_time(tmp_path / "short.mp4") == 0.5


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
            {"persona": "mortimer", "role": "monitor", "kind": "webkit", "x": 960, "y": 720},
        ],
        canvas_width=1920,
        canvas_height=1080,
    )

    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd.count("-i") == 2
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert filter_complex == "[0:v][1:v]overlay=0:0[v]"
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


def test_render_overlay_image_writes_rgba_png_with_transparent_canvas(tmp_path: Path) -> None:
    title = "Alpha"
    subtitle = "Quiet metadata"
    canvas_width = 1920
    canvas_height = 1080
    path = render_overlay_image(
        tmp_path / "overlay.png",
        title=title,
        subtitle=subtitle,
        panes=[],
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    title_width = len(title.upper()) * 6 * 2
    subtitle_width = len(subtitle.upper()) * 6
    box_width = max(title_width, subtitle_width) + (DEFAULT_OVERLAY_BOX.padding * 2)
    box_height = (7 * 2) + 7 + (DEFAULT_OVERLAY_BOX.padding * 2) + 8
    x0 = DEFAULT_OVERLAY_BOX.margin
    y0 = canvas_height - DEFAULT_OVERLAY_BOX.margin - box_height

    assert path.exists()
    # Outside the label box: stays transparent (alpha == 0).
    assert _read_png_pixel(path, 40, 40) == TRANSPARENT
    assert _read_png_pixel(path, x0 + box_width + 8, y0 + 8) == TRANSPARENT
    assert _read_png_pixel(path, x0 + 8, y0 - 8) == TRANSPARENT
    # Inside the label box: alpha matches the default overlay background.
    pixel = _read_png_pixel(path, x0 + 4, y0 + 4)
    assert pixel[3] == DEFAULT_OVERLAY_BOX.background_rgba[3]


def test_render_overlay_image_skips_large_title_card_when_text_is_empty(tmp_path: Path) -> None:
    path = render_overlay_image(
        tmp_path / "overlay.png",
        title="",
        subtitle="",
        panes=[
            {"persona": "p1", "role": "player", "kind": "webkit", "x": 0, "y": 0, "width": 960, "height": 540},
        ],
        canvas_width=1920,
        canvas_height=1080,
    )

    # Far from the pane label: transparent. Inside the pane label box: tinted
    # at the default alpha (the exact x,y here is inside the label rect that
    # _draw_pane_label paints near the bottom-left of pane 1).
    assert _read_png_pixel(path, 24, 24) == TRANSPARENT
    label_pixel = _read_png_pixel(path, 24, 508)
    assert label_pixel[3] == DEFAULT_OVERLAY_BOX.background_rgba[3]
