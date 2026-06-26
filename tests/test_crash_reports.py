# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""macOS .ips crash-report correlation (browser_pool.crash_reports).

A renderer crash is recorded ~60ms in, but the OS writes the matching
``chrome-headless-shell-*.ips`` DiagnosticReport a second or two LATER — so the
SIGSEGV signature can only be attached at status-read time, by matching the
incident timestamp against nearby .ips file mtimes. These tests cover the
parse, the time-window match, the macOS gate, and the status enrichment.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from octowright.browser_pool import crash_reports


def _write_ips(
    directory: Path,
    name: str,
    *,
    signal: str = "SIGSEGV",
    exc_type: str = "EXC_BAD_ACCESS",
    app: str = "chrome-headless-shell",
    exception: bool = True,
) -> Path:
    header = {"app_name": app, "timestamp": "2026-06-26 12:00:01.00 -0700", "bug_type": "309"}
    body: dict[str, object] = {"procName": app}
    if exception:
        body["exception"] = {"type": exc_type, "signal": signal, "codes": "0x1, 0x2"}
    path = directory / name
    path.write_text(json.dumps(header) + "\n" + json.dumps(body))
    return path


def _epoch(iso_z: str) -> float:
    return datetime.fromisoformat(iso_z.replace("Z", "+00:00")).timestamp()


def test_is_macos_reflects_platform() -> None:
    import sys

    assert crash_reports._is_macos() is (sys.platform == "darwin")


def test_parse_ips_extracts_signal_type_and_app(tmp_path: Path) -> None:
    p = _write_ips(tmp_path, "chrome-headless-shell-2026-06-26-120001.ips")
    rep = crash_reports._parse_ips(p)
    assert rep is not None
    assert rep["signal"] == "SIGSEGV"
    assert rep["type"] == "EXC_BAD_ACCESS"
    assert rep["app_name"] == "chrome-headless-shell"
    assert rep["path"] == str(p)


def test_parse_ips_without_exception_block_yields_null_signal(tmp_path: Path) -> None:
    p = _write_ips(tmp_path, "chromium-x.ips", exception=False)
    rep = crash_reports._parse_ips(p)
    assert rep is not None
    assert rep["signal"] is None
    assert rep["type"] is None
    assert rep["app_name"] == "chrome-headless-shell"


def test_parse_ips_malformed_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "chrome-bad.ips"
    p.write_text("this is not an ips file at all")
    assert crash_reports._parse_ips(p) is None


def test_find_crash_report_matches_ips_in_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    crash_ts = "2026-06-26T19:00:00.000Z"
    p = _write_ips(tmp_path, "chrome-headless-shell-x.ips")
    # OS writes the .ips ~2s after the crash — inside the default window.
    os.utime(p, (_epoch(crash_ts) + 2.0, _epoch(crash_ts) + 2.0))
    rep = crash_reports.find_crash_report(crash_ts, reports_dir=tmp_path)
    assert rep is not None and rep["signal"] == "SIGSEGV"


def test_find_crash_report_outside_window_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    crash_ts = "2026-06-26T19:00:00.000Z"
    p = _write_ips(tmp_path, "chrome-headless-shell-x.ips")
    # 10 minutes later — an unrelated crash, must NOT correlate.
    os.utime(p, (_epoch(crash_ts) + 600.0, _epoch(crash_ts) + 600.0))
    assert crash_reports.find_crash_report(crash_ts, reports_dir=tmp_path) is None


def test_find_crash_report_ignores_non_browser_ips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    crash_ts = "2026-06-26T19:00:00.000Z"
    p = _write_ips(tmp_path, "Mail-2026-06-26.ips", app="Mail")
    os.utime(p, (_epoch(crash_ts) + 1.0, _epoch(crash_ts) + 1.0))
    assert crash_reports.find_crash_report(crash_ts, reports_dir=tmp_path) is None


def test_find_crash_report_picks_nearest_far_seen_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Scan order is sorted by name: the FAR report sorts first (sets best), then
    # the nearer one updates it — covers the "delta < best" replace branch.
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    crash_ts = "2026-06-26T19:00:00.000Z"
    far = _write_ips(tmp_path, "chrome-a-far.ips", signal="SIGABRT")
    near = _write_ips(tmp_path, "chrome-z-near.ips", signal="SIGSEGV")
    os.utime(far, (_epoch(crash_ts) + 25.0, _epoch(crash_ts) + 25.0))
    os.utime(near, (_epoch(crash_ts) + 2.0, _epoch(crash_ts) + 2.0))
    rep = crash_reports.find_crash_report(crash_ts, reports_dir=tmp_path)
    assert rep is not None and rep["signal"] == "SIGSEGV"


def test_find_crash_report_picks_nearest_near_seen_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The NEAR report sorts first (sets best), then the farther one is skipped —
    # covers the "delta not closer, keep best" branch.
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    crash_ts = "2026-06-26T19:00:00.000Z"
    near = _write_ips(tmp_path, "chrome-a-near.ips", signal="SIGSEGV")
    far = _write_ips(tmp_path, "chrome-z-far.ips", signal="SIGABRT")
    os.utime(near, (_epoch(crash_ts) + 2.0, _epoch(crash_ts) + 2.0))
    os.utime(far, (_epoch(crash_ts) + 25.0, _epoch(crash_ts) + 25.0))
    rep = crash_reports.find_crash_report(crash_ts, reports_dir=tmp_path)
    assert rep is not None and rep["signal"] == "SIGSEGV"


def test_find_crash_report_skips_unstatable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A dangling symlink named like a browser crash: glob yields it, stat() raises
    # OSError, and the scan skips it instead of blowing up the status call.
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    link = tmp_path / "chrome-dangling.ips"
    link.symlink_to(tmp_path / "missing-target")
    assert crash_reports.find_crash_report("2026-06-26T19:00:00.000Z", reports_dir=tmp_path) is None


def test_find_crash_report_non_macos_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: False)
    crash_ts = "2026-06-26T19:00:00.000Z"
    p = _write_ips(tmp_path, "chrome-headless-shell-x.ips")
    os.utime(p, (_epoch(crash_ts) + 2.0, _epoch(crash_ts) + 2.0))
    assert crash_reports.find_crash_report(crash_ts, reports_dir=tmp_path) is None


def test_find_crash_report_missing_dir_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    assert crash_reports.find_crash_report("2026-06-26T19:00:00.000Z", reports_dir=tmp_path / "nope") is None


def test_find_crash_report_bad_timestamp_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    _write_ips(tmp_path, "chrome-headless-shell-x.ips")
    assert crash_reports.find_crash_report("not-a-timestamp", reports_dir=tmp_path) is None


def test_enrich_attaches_report_to_renderer_crash_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    crash_ts = "2026-06-26T19:00:00.000Z"
    p = _write_ips(tmp_path, "chrome-headless-shell-x.ips")
    os.utime(p, (_epoch(crash_ts) + 2.0, _epoch(crash_ts) + 2.0))
    incs = [
        {"category": "renderer_crash", "ts": crash_ts, "outcome": "recovered", "instance_id": "a"},
        {"category": "driver_restart", "ts": crash_ts, "restart_count": 1},
    ]
    out = crash_reports.enrich(incs, reports_dir=tmp_path)
    assert out[0]["crash_report"]["signal"] == "SIGSEGV"
    assert "crash_report" not in out[1]
    # Pure: original dicts not mutated in place.
    assert "crash_report" not in incs[0]


def test_enrich_non_macos_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: False)
    incs = [{"category": "renderer_crash", "ts": "2026-06-26T19:00:00.000Z"}]
    out = crash_reports.enrich(incs, reports_dir=Path("/nonexistent"))
    assert out is incs


def test_enrich_no_matching_report_leaves_incident_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crash_reports, "_is_macos", lambda: True)
    incs = [{"category": "renderer_crash", "ts": "2026-06-26T19:00:00.000Z", "outcome": "failed"}]
    out = crash_reports.enrich(incs, reports_dir=tmp_path)
    assert "crash_report" not in out[0]
