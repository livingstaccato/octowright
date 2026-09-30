# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The recording and websocket disk ceilings are ON by default.

Both were off (unbounded), so a remote page -- a console firehose, a socket
pushing frames forever -- could fill the recordings disk from one open tab.
The defaults are generous (512 MiB per recording, 256 MiB per websocket
sidecar) and stay configurable exactly as before: a positive byte count sets
the ceiling, ``0``/a falsey token removes it, and an unparsable value falls
back to the DEFAULT -- a typo must not silently remove a disk guard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.recorder import RECORDING_MAX_BYTES_DEFAULT, Recorder, _recording_max_bytes
from octowright.session.core_io_mixin import WEBSOCKET_MAX_BYTES_DEFAULT, _websocket_max_bytes
from tests.test_session_io_mixin_branches import _make_subject

FALSEY = ["0", "off", "OFF", "false", "no", "never", "none", "disabled"]


def test_the_defaults() -> None:
    assert RECORDING_MAX_BYTES_DEFAULT == 512 * 1024 * 1024
    assert WEBSOCKET_MAX_BYTES_DEFAULT == 256 * 1024 * 1024


@pytest.mark.parametrize(
    ("env", "parse", "default"),
    [
        ("OCTOWRIGHT_RECORDING_MAX_BYTES", _recording_max_bytes, RECORDING_MAX_BYTES_DEFAULT),
        ("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", _websocket_max_bytes, WEBSOCKET_MAX_BYTES_DEFAULT),
    ],
)
class TestParsing:
    def test_unset_applies_the_default(self, monkeypatch: pytest.MonkeyPatch, env: str, parse, default: int) -> None:
        monkeypatch.delenv(env, raising=False)
        assert parse() == default

    def test_empty_applies_the_default(self, monkeypatch: pytest.MonkeyPatch, env: str, parse, default: int) -> None:
        monkeypatch.setenv(env, "  ")
        assert parse() == default

    @pytest.mark.parametrize("token", FALSEY)
    def test_zero_or_falsey_is_unlimited(
        self, monkeypatch: pytest.MonkeyPatch, env: str, parse, default: int, token: str
    ) -> None:
        monkeypatch.setenv(env, token)
        assert parse() == 0

    def test_a_positive_count_is_honoured(self, monkeypatch: pytest.MonkeyPatch, env: str, parse, default: int) -> None:
        monkeypatch.setenv(env, "4096")
        assert parse() == 4096

    @pytest.mark.parametrize("bad", ["banana", "3.5", "-1", "1e9", "512MiB"])
    def test_unparsable_falls_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch, env: str, parse, default: int, bad: str
    ) -> None:
        monkeypatch.setenv(env, bad)
        assert parse() == default


def test_the_recording_default_writes_one_marker_and_keeps_going(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hit the (shrunk) default: one marker with the limit, then silent drops."""
    monkeypatch.delenv("OCTOWRIGHT_RECORDING_MAX_BYTES", raising=False)
    monkeypatch.setattr("octowright.recorder.RECORDING_MAX_BYTES_DEFAULT", 400)
    path = tmp_path / "r.jsonl"
    rec = Recorder(path)
    for i in range(100):
        rec.record("console", text=f"spam {i:04d}")
    rec.close()

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    markers = [row for row in rows if row["action"] == "recording_truncated"]
    assert len(markers) == 1 and markers[0]["limit_bytes"] == 400
    assert rows[-1]["action"] == "recording_truncated"


def test_the_websocket_default_writes_one_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", raising=False)
    monkeypatch.setattr("octowright.session.core_io_mixin.WEBSOCKET_MAX_BYTES_DEFAULT", 300)
    subj = _make_subject(tmp_path)
    for i in range(50):
        subj._append_websocket_cache(direction="framesent", id_=i, url="ws://x", payload="z" * 100, payload_size=100)
    subj._websocket_fh.flush()

    rows = [json.loads(line) for line in subj.websocket_path.read_text().splitlines()]
    markers = [row for row in rows if row["action"] == "websocket_truncated"]
    assert len(markers) == 1 and markers[0]["limit_bytes"] == 300
