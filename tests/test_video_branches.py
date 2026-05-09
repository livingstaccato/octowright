# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.video.

Complements tests/test_video.py by pinning the branches the existing
suite leaves untested:
- optimize_png argv shape + temp-file replacement
- transcode_video argv shape (libx264, faststart)
- compose_video_grid validation errors (empty / columns<1)
- compose_video_layout validation errors (empty)
- render_supporting_video poster-optimize branch (>500KB)
- poster_capture_time: probe failure, zero / negative / small / large duration
- probe_video: ffprobe missing, exit non-zero, empty streams, missing fields
- _run_ffmpeg failure path (RuntimeError contains stderr)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright import video as _video


@pytest.fixture
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Capture every subprocess.run invocation; pretend ffmpeg/ffprobe both on PATH."""
    runs: list[dict[str, Any]] = []

    def fake_which(name: str) -> str | None:
        if name in {"ffmpeg", "ffprobe"}:
            return f"/usr/bin/{name}"
        return None

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        runs.append({"cmd": cmd, "kwargs": kwargs})
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(_video.shutil, "which", fake_which)
    monkeypatch.setattr(_video.subprocess, "run", fake_run)
    rec = MagicMock()
    rec.runs = runs
    return rec


# ─── _run_ffmpeg ─────────────────────────────────────────────────────────────


class TestRunFfmpegFailure:
    def test_nonzero_exit_raises_runtime_error_with_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-zero ffmpeg → RuntimeError including stderr."""
        monkeypatch.setattr(
            _video.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=2, stdout="", stderr="boom\nbad-arg"),
        )
        with pytest.raises(RuntimeError) as exc:
            _video._run_ffmpeg(["ffmpeg", "-i", "x"])
        msg = str(exc.value)
        assert "exited 2" in msg
        assert "boom" in msg


# ─── ensure_ffmpeg ───────────────────────────────────────────────────────────


class TestEnsureFfmpegMessage:
    def test_missing_message_includes_install_hints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Error string mentions 'brew install ffmpeg' and 'apt-get install ffmpeg'."""
        monkeypatch.setattr(_video.shutil, "which", lambda _name: None)
        with pytest.raises(RuntimeError) as exc:
            _video.ensure_ffmpeg()
        msg = str(exc.value)
        assert "brew install ffmpeg" in msg
        assert "apt-get install ffmpeg" in msg


# ─── optimize_png ────────────────────────────────────────────────────────────


class TestOptimizePng:
    def test_argv_includes_scale_filter(self, fake_ffmpeg: MagicMock, tmp_path: Path) -> None:
        """optimize_png builds a -vf scale filter capping width."""
        png = tmp_path / "x.png"
        png.write_bytes(b"")
        # Pretend the optimized file gets created so .replace() finds it.
        opt_path = png.with_suffix(".optimized.png")
        opt_path.write_bytes(b"optimized")
        result = _video.optimize_png(png, max_width=640)
        assert result == png
        cmd = fake_ffmpeg.runs[-1]["cmd"]
        # -vf scale='min(iw,640)':-1
        assert "-vf" in cmd
        i = cmd.index("-vf")
        assert "min(iw,640)" in cmd[i + 1]

    def test_default_max_width_960(self, fake_ffmpeg: MagicMock, tmp_path: Path) -> None:
        """Default max_width is 960."""
        png = tmp_path / "x.png"
        png.write_bytes(b"")
        opt_path = png.with_suffix(".optimized.png")
        opt_path.write_bytes(b"optimized")
        _video.optimize_png(png)
        cmd = fake_ffmpeg.runs[-1]["cmd"]
        i = cmd.index("-vf")
        assert "min(iw,960)" in cmd[i + 1]

    def test_argv_emits_one_frame_only(self, fake_ffmpeg: MagicMock, tmp_path: Path) -> None:
        """`-frames:v 1` is in the argv."""
        png = tmp_path / "x.png"
        png.write_bytes(b"")
        opt_path = png.with_suffix(".optimized.png")
        opt_path.write_bytes(b"optimized")
        _video.optimize_png(png)
        cmd = fake_ffmpeg.runs[-1]["cmd"]
        assert "-frames:v" in cmd
        i = cmd.index("-frames:v")
        assert cmd[i + 1] == "1"

    def test_replaces_input_with_optimized(self, fake_ffmpeg: MagicMock, tmp_path: Path) -> None:
        """The .optimized.png temp file is moved over the input path."""
        png = tmp_path / "x.png"
        png.write_bytes(b"original")
        opt_path = png.with_suffix(".optimized.png")
        opt_path.write_bytes(b"optimized-content")
        _video.optimize_png(png)
        # Original was overwritten by the optimized temp.
        assert png.read_bytes() == b"optimized-content"
        assert not opt_path.exists()


# ─── transcode_video ─────────────────────────────────────────────────────────


class TestTranscodeVideo:
    def test_argv_uses_libx264_yuv420p_faststart(self, fake_ffmpeg: MagicMock, tmp_path: Path) -> None:
        """All three quality knobs present in the argv."""
        src = tmp_path / "in.webm"
        src.write_bytes(b"")
        dst = tmp_path / "out.mp4"
        result = _video.transcode_video(src, dst)
        assert result == dst
        cmd = fake_ffmpeg.runs[-1]["cmd"]
        assert "-c:v" in cmd
        i = cmd.index("-c:v")
        assert cmd[i + 1] == "libx264"
        assert "-pix_fmt" in cmd
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
        assert "-movflags" in cmd
        assert cmd[cmd.index("-movflags") + 1] == "+faststart"

    def test_creates_target_parent_dir(self, fake_ffmpeg: MagicMock, tmp_path: Path) -> None:
        """target_path.parent is created."""
        src = tmp_path / "in.webm"
        src.write_bytes(b"")
        dst = tmp_path / "deep" / "nested" / "out.mp4"
        _video.transcode_video(src, dst)
        assert dst.parent.exists()

    def test_argv_ends_with_y_flag(self, fake_ffmpeg: MagicMock, tmp_path: Path) -> None:
        """`-y` (overwrite) is the trailing argument."""
        src = tmp_path / "in.webm"
        src.write_bytes(b"")
        dst = tmp_path / "out.mp4"
        _video.transcode_video(src, dst)
        assert fake_ffmpeg.runs[-1]["cmd"][-1] == "-y"


# ─── compose_video_grid validation ──────────────────────────────────────────


class TestComposeVideoGridValidation:
    def test_empty_sources_raises(self, tmp_path: Path) -> None:
        """compose_video_grid([]) → ValueError listing 'at least one'."""
        with pytest.raises(ValueError, match=r"at least one source video"):
            _video.compose_video_grid([], tmp_path / "out.mp4", columns=2, cell_width=320, cell_height=240)

    def test_columns_zero_raises(self, tmp_path: Path) -> None:
        """columns=0 → ValueError."""
        with pytest.raises(ValueError, match=r"columns must be"):
            _video.compose_video_grid(
                [tmp_path / "a.mp4"], tmp_path / "out.mp4", columns=0, cell_width=320, cell_height=240
            )

    def test_columns_negative_raises(self, tmp_path: Path) -> None:
        """columns=-1 → ValueError (the < 1 boundary)."""
        with pytest.raises(ValueError, match=r"columns must be"):
            _video.compose_video_grid(
                [tmp_path / "a.mp4"], tmp_path / "out.mp4", columns=-1, cell_width=320, cell_height=240
            )


# ─── compose_video_layout validation ────────────────────────────────────────


class TestComposeVideoLayoutValidation:
    def test_empty_placements_raises(self, tmp_path: Path) -> None:
        """No placements → ValueError mentioning 'at least one placement'."""
        with pytest.raises(ValueError, match=r"at least one placement"):
            _video.compose_video_layout([], tmp_path / "out.mp4")

    def test_layout_argv_contains_xstack_with_per_placement_offset(
        self, fake_ffmpeg: MagicMock, tmp_path: Path
    ) -> None:
        """Each placement contributes its x_y to the xstack layout."""
        placements: list[Any] = [
            {"source": tmp_path / "a.mp4", "width": 320, "height": 240, "x": 0, "y": 0},
            {"source": tmp_path / "b.mp4", "width": 320, "height": 240, "x": 320, "y": 0},
        ]
        _video.compose_video_layout(placements, tmp_path / "out.mp4")
        cmd = fake_ffmpeg.runs[-1]["cmd"]
        i = cmd.index("-filter_complex")
        fc = cmd[i + 1]
        assert "0_0" in fc
        assert "320_0" in fc
        assert "xstack=inputs=2" in fc


# ─── render_supporting_video poster-optimize branch ─────────────────────────


class TestRenderSupportingVideoOptimize:
    def test_optimizes_poster_when_over_threshold(
        self, fake_ffmpeg: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Poster >500KB triggers optimize_png."""
        src = tmp_path / "in.webm"
        src.write_bytes(b"")
        dst = tmp_path / "out.mp4"
        poster = tmp_path / "poster.png"

        # Shape: extract_frame writes the poster file. Stub it via fake.
        def fake_extract(video_path: Path, out_path: Path, *, at_time: float = 0.5) -> Path:
            # Write a >500KB file to trigger the optimize branch.
            out_path.write_bytes(b"x" * 600_000)
            return out_path

        monkeypatch.setattr(_video, "extract_frame", fake_extract)

        called: list[Path] = []

        def fake_optimize(path: Path, *, max_width: int = 960) -> Path:
            called.append(path)
            return path

        monkeypatch.setattr(_video, "optimize_png", fake_optimize)
        _video.render_supporting_video(src, dst, poster_path=poster)
        assert called == [poster]

    def test_no_optimize_when_under_threshold(
        self, fake_ffmpeg: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Small poster → optimize_png not called."""
        src = tmp_path / "in.webm"
        src.write_bytes(b"")
        dst = tmp_path / "out.mp4"
        poster = tmp_path / "poster.png"

        def fake_extract(video_path: Path, out_path: Path, *, at_time: float = 0.5) -> Path:
            out_path.write_bytes(b"small")
            return out_path

        monkeypatch.setattr(_video, "extract_frame", fake_extract)
        called: list[Path] = []
        monkeypatch.setattr(_video, "optimize_png", lambda p, **_kw: called.append(p))
        _video.render_supporting_video(src, dst, poster_path=poster)
        assert called == []


# ─── poster_capture_time ─────────────────────────────────────────────────────


class TestPosterCaptureTime:
    def test_probe_failure_returns_default_half_second(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """probe_video raise → fallback to 0.5."""
        monkeypatch.setattr(_video, "probe_video", lambda _p: (_ for _ in ()).throw(RuntimeError("nope")))
        assert _video.poster_capture_time(tmp_path / "x.mp4") == 0.5

    def test_zero_duration_returns_half_second(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Duration 0 → fallback 0.5."""
        monkeypatch.setattr(_video, "probe_video", lambda _p: {"duration_seconds": 0.0})
        assert _video.poster_capture_time(tmp_path / "x.mp4") == 0.5

    def test_negative_duration_returns_half_second(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Negative duration → fallback 0.5."""
        monkeypatch.setattr(_video, "probe_video", lambda _p: {"duration_seconds": -10})
        assert _video.poster_capture_time(tmp_path / "x.mp4") == 0.5

    def test_missing_duration_field_returns_half_second(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """No 'duration_seconds' key → 0.5."""
        monkeypatch.setattr(_video, "probe_video", lambda _p: {"width": 100})
        assert _video.poster_capture_time(tmp_path / "x.mp4") == 0.5

    def test_short_clip_uses_half_duration(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """1-second clip → 0.5s (the clamped lower bound from max(0.5, 1.0/2.0))."""
        monkeypatch.setattr(_video, "probe_video", lambda _p: {"duration_seconds": 1.0})
        assert _video.poster_capture_time(tmp_path / "x.mp4") == 0.5

    def test_mid_clip_uses_half_duration(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """3-second clip → 1.5s (mid)."""
        monkeypatch.setattr(_video, "probe_video", lambda _p: {"duration_seconds": 3.0})
        assert _video.poster_capture_time(tmp_path / "x.mp4") == 1.5

    def test_long_clip_caps_at_two_seconds(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """30-second clip → capped at 2.0s."""
        monkeypatch.setattr(_video, "probe_video", lambda _p: {"duration_seconds": 30.0})
        assert _video.poster_capture_time(tmp_path / "x.mp4") == 2.0


# ─── probe_video ─────────────────────────────────────────────────────────────


class TestProbeVideo:
    def test_missing_ffprobe_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """ffprobe absent → RuntimeError mentioning 'install ffmpeg tools'."""
        monkeypatch.setattr(_video.shutil, "which", lambda name: None if name == "ffprobe" else "/x/ffmpeg")
        with pytest.raises(RuntimeError, match=r"ffprobe not found"):
            _video.probe_video(tmp_path / "x.mp4")

    def test_nonzero_exit_raises_with_stderr(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """ffprobe non-zero → RuntimeError including stderr."""
        monkeypatch.setattr(_video.shutil, "which", lambda _n: "/x/ffprobe")
        monkeypatch.setattr(
            _video.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="bad-input"),
        )
        with pytest.raises(RuntimeError, match=r"ffprobe exited 1"):
            _video.probe_video(tmp_path / "x.mp4")

    def test_returns_width_height_duration(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Happy path parses streams[0] dimensions + format.duration."""
        monkeypatch.setattr(_video.shutil, "which", lambda _n: "/x/ffprobe")
        payload = json.dumps({"streams": [{"width": 1920, "height": 1080}], "format": {"duration": "12.34"}})
        monkeypatch.setattr(
            _video.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        )
        result = _video.probe_video(tmp_path / "x.mp4")
        assert result == {"width": 1920, "height": 1080, "duration_seconds": 12.34}

    def test_empty_streams_returns_zeros(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """`streams: []` falls through to zeroed defaults."""
        monkeypatch.setattr(_video.shutil, "which", lambda _n: "/x/ffprobe")
        payload = json.dumps({"streams": [], "format": {"duration": "5.0"}})
        monkeypatch.setattr(
            _video.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        )
        result = _video.probe_video(tmp_path / "x.mp4")
        assert result["width"] == 0
        assert result["height"] == 0
        assert result["duration_seconds"] == 5.0

    def test_missing_keys_yield_zero_defaults(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """All fields missing → all zeros."""
        monkeypatch.setattr(_video.shutil, "which", lambda _n: "/x/ffprobe")
        monkeypatch.setattr(
            _video.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=0, stdout="{}", stderr=""),
        )
        result = _video.probe_video(tmp_path / "x.mp4")
        assert result == {"width": 0, "height": 0, "duration_seconds": 0.0}

    def test_empty_stdout_treated_as_empty_object(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Empty stdout → '{}' fallback so json.loads doesn't crash."""
        monkeypatch.setattr(_video.shutil, "which", lambda _n: "/x/ffprobe")
        monkeypatch.setattr(
            _video.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        result = _video.probe_video(tmp_path / "x.mp4")
        assert result == {"width": 0, "height": 0, "duration_seconds": 0.0}
