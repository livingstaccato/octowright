# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof of ``octowright test --sequence ... --record-video``.

Runs the real CLI in a child interpreter against a real engine, with every
octowright path pointed under ``tmp_path``. The sequence FAILS on its second
step, because a failed run's video is the one an operator actually wants: the
test asserts the run exits 1 and still leaves a complete, non-empty WebM under
the stable name, and prints its path after the report line. It runs as a persona, the way the lab does, so the
browser is a persistent context.

A second test runs a PASSING sequence with ``OCTOWRIGHT_VIEWPORT_W/H`` set to
1920x1080 and ``OCTOWRIGHT_MACRO_SLOWMO_MS`` set: the page must report that
viewport, the WebM's own track header must carry the same frame size (read with
the stdlib EBML walk below, no ffprobe), and the JUnit time must include the
per-action delay.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 -- runs this interpreter on fixed arguments
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_PAINT = {
    "name": "paint",
    "description": "paint something worth recording",
    "actions": [
        {
            "action": "evaluate",
            "expression": "document.body.innerHTML = '<h1 style=\"font-size:80px\">recorded</h1>'",
        },
        {"action": "evaluate", "expression": "new Promise(r => setTimeout(r, 800))"},
    ],
}
_BREAK = {
    "name": "break",
    "description": "fail on purpose",
    "actions": [{"action": "evaluate", "expression": "(() => { throw new Error('boom') })()"}],
}
_SIZED = {
    "name": "sized",
    "description": "prove the viewport from inside the page",
    "actions": [
        {"action": "evaluate", "expression": "document.body.innerHTML = '<h1>sized</h1>'"},
        {"action": "expect_js", "expression": "`${innerWidth}x${innerHeight}`", "equals": "1920x1080"},
    ],
}
_SLOWMO_MS = 500
_UNAVAILABLE = ("executable doesn't exist", "missing x server", "cannot open display", "host system is missing")


def _env(tmp_path: Path) -> dict[str, str]:
    # XDG state/config too: a chromium launch writes its new-tab extension under
    # the user state dir, which is the running daemon's otherwise. The cache dir
    # is left alone -- Playwright finds its browsers there.
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path / "xdg-state"), XDG_CONFIG_HOME=str(tmp_path / "xdg-config"))
    for key, rel in {
        "OCTOWRIGHT_RECORDINGS": "recordings",
        "OCTOWRIGHT_PROFILES_DIR": "profiles",
        "OCTOWRIGHT_MACROS_DIR": "macros",
        "OCTOWRIGHT_SCENARIOS_DIR": "scenarios",
        "OCTOWRIGHT_CAPTURES_DIR": "captures",
        "OCTOWRIGHT_GOLDENS_DIR": "goldens",
        "OCTOWRIGHT_UPLOAD_STAGING_DIR": "uploads",
        "OCTOWRIGHT_SESSION_MANIFEST": "state/session-manifest.json",
        "OCTOWRIGHT_ADVISOR_STATE": "state/advisor.json",
        "OCTOWRIGHT_LOCK_PATH": "state/octowright.lock",
        "OCTOWRIGHT_BRIDGE_STATE": "state/bridge-state.json",
        "OCTOWRIGHT_UPGRADE_STATE": "state/upgrade.json",
    }.items():
        env[key] = str(tmp_path / rel)
    return env


@pytest.mark.live_browser
@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
def test_a_failed_sequence_leaves_a_real_video(tmp_path: Path, kind: str) -> None:
    pytest.importorskip("playwright")
    macros = tmp_path / "macros"
    macros.mkdir()
    for macro in (_PAINT, _BREAK):
        (macros / f"{macro['name']}.json").write_text(json.dumps(macro))
    sequence = tmp_path / "repair.json"
    sequence.write_text(json.dumps([{"macro": "paint"}, {"macro": "break"}]))
    artifacts = tmp_path / "recordings" / "evidence"
    # A persona, as the lab runs it: its browser is a persistent context.
    (tmp_path / "profiles" / "lab").mkdir(parents=True)
    (tmp_path / "profiles" / "lab" / "profile.yaml").write_text("name: lab\n")

    proc = subprocess.run(  # nosec B603 -- fixed argv, no shell
        [sys.executable, "-c", "from octowright.cli import main; main()", "test", "--kind", kind,
         "--persona", "lab", "--sequence", str(sequence), "--artifacts", str(artifacts), "--redact-errors", "--record-video"],
        env=_env(tmp_path), cwd=tmp_path, capture_output=True, text=True, timeout=180, check=False,
    )  # fmt: skip
    if proc.returncode != 1 and any(s in (proc.stdout + proc.stderr).lower() for s in _UNAVAILABLE):
        pytest.skip(f"{kind} unavailable here: {proc.stderr[-400:]}")

    video = (artifacts / "repair.webm").resolve()
    assert proc.returncode == 1, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[-2:] == [f"report: {(artifacts / 'octowright-report.xml').resolve()}", f"video: {video}"], lines
    assert video.stat().st_size > 0
    assert video.read_bytes()[:4] == bytes.fromhex("1a45dfa3")  # EBML: a complete WebM header


# EBML ids on the path Segment > Tracks > TrackEntry > Video > PixelWidth/Height.
_EBML_MASTERS = {0x18538067, 0x1654AE6B, 0xAE, 0xE0}
_PIXEL_WIDTH, _PIXEL_HEIGHT, _CLUSTER = 0xB0, 0xBA, 0x1F43B675


def _vint(data: bytes, pos: int, *, strip: bool) -> tuple[int, int, bool]:
    """One EBML variable-length integer: (value, next position, is "unknown size")."""
    length = 1
    while length <= 8 and not data[pos] & (0x80 >> (length - 1)):
        length += 1
    value = data[pos] & ((0x80 >> (length - 1)) - 1) if strip else data[pos]
    for byte in data[pos + 1 : pos + length]:
        value = (value << 8) | byte
    return value, pos + length, strip and value == (1 << (7 * length)) - 1


def webm_frame_size(data: bytes) -> tuple[int | None, int | None]:
    """The first video track's PixelWidth x PixelHeight, from the WebM header.

    Descends only the masters on the path to the track's Video element and
    skips everything else by its size; stops at the first Cluster, since the
    Tracks element precedes the media. Unknown-size masters (a live-written
    Segment) are descended like any other.
    """
    pos, found = 0, {}
    while pos < len(data) and len(found) < 2:
        element, pos, _ = _vint(data, pos, strip=False)
        size, pos, unknown = _vint(data, pos, strip=True)
        if element == _CLUSTER or (unknown and element not in _EBML_MASTERS):
            break
        if element in _EBML_MASTERS:
            continue
        if element in (_PIXEL_WIDTH, _PIXEL_HEIGHT):
            found[element] = int.from_bytes(data[pos : pos + size], "big")
        pos += size
    return found.get(_PIXEL_WIDTH), found.get(_PIXEL_HEIGHT)


@pytest.mark.live_browser
@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
def test_the_video_is_recorded_at_the_configured_viewport(tmp_path: Path, kind: str) -> None:
    pytest.importorskip("playwright")
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sized.json").write_text(json.dumps(_SIZED))
    sequence = tmp_path / "wide.json"
    sequence.write_text(json.dumps([{"macro": "sized"}]))
    artifacts = tmp_path / "recordings" / "evidence"
    env = dict(
        _env(tmp_path),
        OCTOWRIGHT_VIEWPORT_W="1920",
        OCTOWRIGHT_VIEWPORT_H="1080",
        OCTOWRIGHT_MACRO_SLOWMO_MS=str(_SLOWMO_MS),
    )

    proc = subprocess.run(  # nosec B603 -- fixed argv, no shell
        [sys.executable, "-c", "from octowright.cli import main; main()", "test", "--kind", kind,
         "--sequence", str(sequence), "--artifacts", str(artifacts), "--record-video"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=180, check=False,
    )  # fmt: skip
    if proc.returncode != 0 and any(s in (proc.stdout + proc.stderr).lower() for s in _UNAVAILABLE):
        pytest.skip(f"{kind} unavailable here: {proc.stderr[-400:]}")

    # Exit 0 means the page's own expect_js saw 1920x1080.
    assert proc.returncode == 0, proc.stdout + proc.stderr
    video = artifacts / "wide.webm"
    assert webm_frame_size(video.read_bytes()) == (1920, 1080)
    case = ET.parse(artifacts / "octowright-report.xml").getroot().find("testcase")
    assert case is not None
    # Two actions, each delayed by the env slowmo before it dispatched.
    assert float(case.get("time", "0")) >= 2 * _SLOWMO_MS / 1000
