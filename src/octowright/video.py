# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def ensure_ffmpeg() -> str:
    """Return the absolute path to ffmpeg. Raises RuntimeError if missing."""
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError(
            "ffmpeg not found on PATH; install with `brew install ffmpeg` (macOS) "
            "or `apt-get install ffmpeg` (Linux). Required by extract_frames to "
            "decode the recorded video into still frames."
        )
    return found


def extract_frames(
    video_path: Path,
    out_dir: Path,
    *,
    fps: float | None = None,
    at_times: list[float] | None = None,
) -> list[Path]:
    """Extract frames from a video file using ffmpeg.

    Exactly one of fps / at_times must be set.

    - fps: extract N frames/second. Frames land at out_dir/frame-%04d.png.
    - at_times: extract one frame at each second-timestamp. Frames land at
      out_dir/frame-{index:03d}-t{time:.3f}.png.

    Returns the sorted list of produced Paths.
    Raises RuntimeError if ffmpeg is missing or exits non-zero.
    """
    if fps is None and at_times is None:
        raise ValueError("supply exactly one of fps or at_times")
    if fps is not None and at_times is not None:
        raise ValueError("supply exactly one of fps or at_times, not both")

    ffmpeg = ensure_ffmpeg()
    out_dir.mkdir(parents=True, exist_ok=True)

    if fps is not None:
        return _extract_by_fps(ffmpeg, video_path, out_dir, fps)
    else:
        assert at_times is not None
        return _extract_at_times(ffmpeg, video_path, out_dir, at_times)


def _run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg exited {result.returncode}:\n{result.stderr}")


def _extract_by_fps(ffmpeg: str, video_path: Path, out_dir: Path, fps: float) -> list[Path]:
    pattern = str(out_dir / "frame-%04d.png")
    cmd = [
        ffmpeg,
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps}",
        "-vsync",
        "vfr",
        pattern,
        "-y",
    ]
    _run_ffmpeg(cmd)
    return sorted(out_dir.glob("frame-*.png"))


def _extract_at_times(ffmpeg: str, video_path: Path, out_dir: Path, at_times: list[float]) -> list[Path]:
    produced: list[Path] = []
    for idx, t in enumerate(at_times):
        out_file = out_dir / f"frame-{idx:03d}-t{t:.3f}.png"
        cmd = [
            ffmpeg,
            "-ss",
            str(t),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(out_file),
            "-y",
        ]
        _run_ffmpeg(cmd)
        produced.append(out_file)
    return sorted(produced)


def transcode_video(source_path: Path, target_path: Path) -> Path:
    ffmpeg = ensure_ffmpeg()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-i",
        str(source_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(target_path),
        "-y",
    ]
    _run_ffmpeg(cmd)
    return target_path


def optimize_png(path: Path, *, max_width: int = 960) -> Path:
    ffmpeg = ensure_ffmpeg()
    temp_path = path.with_suffix(".optimized.png")
    cmd = [
        ffmpeg,
        "-i",
        str(path),
        "-vf",
        f"scale='min(iw,{max_width})':-1",
        "-frames:v",
        "1",
        str(temp_path),
        "-y",
    ]
    _run_ffmpeg(cmd)
    temp_path.replace(path)
    return path


def extract_frame(video_path: Path, out_path: Path, *, at_time: float = 0.5) -> Path:
    ffmpeg = ensure_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-ss",
        str(at_time),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        str(out_path),
        "-y",
    ]
    _run_ffmpeg(cmd)
    return out_path


def compose_video_grid(
    source_paths: list[Path],
    target_path: Path,
    *,
    columns: int,
    cell_width: int,
    cell_height: int,
) -> Path:
    if not source_paths:
        raise ValueError("compose_video_grid requires at least one source video")
    if columns < 1:
        raise ValueError("columns must be >= 1")

    ffmpeg = ensure_ffmpeg()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filters: list[str] = []
    layouts: list[str] = []

    for index, source_path in enumerate(source_paths):
        inputs.extend(["-i", str(source_path)])
        filters.append(
            f"[{index}:v]setpts=PTS-STARTPTS,"
            f"scale={cell_width}:{cell_height}:force_original_aspect_ratio=decrease,"
            f"pad={cell_width}:{cell_height}:(ow-iw)/2:(oh-ih)/2:black[v{index}]"
        )
        col = index % columns
        row = index // columns
        layouts.append(f"{col * cell_width}_{row * cell_height}")

    filter_complex = ";".join(filters) + ";" + "".join(f"[v{index}]" for index in range(len(source_paths)))
    filter_complex += f"xstack=inputs={len(source_paths)}:layout={'|'.join(layouts)}[v]"
    cmd = [
        ffmpeg,
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(target_path),
        "-y",
    ]
    _run_ffmpeg(cmd)
    return target_path


def compose_video_layout(
    placements: list[dict[str, int | Path]],
    target_path: Path,
) -> Path:
    if not placements:
        raise ValueError("compose_video_layout requires at least one placement")

    ffmpeg = ensure_ffmpeg()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filters: list[str] = []
    layouts: list[str] = []

    for index, placement in enumerate(placements):
        source_path = placement["source"]
        width = int(placement["width"])
        height = int(placement["height"])
        x = int(placement["x"])
        y = int(placement["y"])
        inputs.extend(["-i", str(source_path)])
        filters.append(
            f"[{index}:v]setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[v{index}]"
        )
        layouts.append(f"{x}_{y}")

    filter_complex = ";".join(filters) + ";" + "".join(f"[v{index}]" for index in range(len(placements)))
    filter_complex += f"xstack=inputs={len(placements)}:layout={'|'.join(layouts)}[v]"
    cmd = [
        ffmpeg,
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(target_path),
        "-y",
    ]
    _run_ffmpeg(cmd)
    return target_path


def probe_video(path: Path) -> dict[str, float | int]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found on PATH; install ffmpeg tools first")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe exited {result.returncode}:\n{result.stderr}")
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or [{}]
    stream = streams[0] if streams else {}
    format_info = payload.get("format") or {}
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration_seconds": float(format_info.get("duration") or 0.0),
    }
