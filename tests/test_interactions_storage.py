# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

#
"""Bounded private interaction storage and resumable disk-reader contracts."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest


def backend() -> Any:
    try:
        return importlib.import_module("octowright.interaction_storage")
    except ModuleNotFoundError:
        pytest.fail("bounded interaction storage is not implemented")


def row(n: int = 1) -> dict[str, Any]:
    return {
        "type": "interaction",
        "v": 1,
        "page_id": 1,
        "video": None,
        "t": n,
        "phase": "down",
        "pointer_type": "mouse",
        "pointer_id": n,
        "button": 0,
        "x": 10,
        "y": 20,
        "width": 800,
        "height": 600,
        "at": 1795481334000 + n,
        "shown": False,
    }


def test_private_append_cursor_and_missing(tmp_path: Path) -> None:
    api = backend()
    log = tmp_path / "2026-chromium-abc.jsonl"
    assert api.read_interactions(log) == {
        "available": False,
        "events": [],
        "next_cursor": 0,
        "complete": True,
        "truncated": False,
    }
    sink = api.InteractionSink(log)
    assert sink.append(row(1))
    assert sink.append(row(2))
    sink.close()
    path = log.with_suffix(".interactions.jsonl")
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    first = api.read_interactions(log, limit=1)
    assert first["events"] == [row(1)] and not first["complete"]
    second = api.read_interactions(log, cursor=first["next_cursor"], limit=1)
    assert second["events"] == [row(2)] and second["complete"]
    assert second["next_cursor"] == path.stat().st_size


def test_cap_emits_terminal_truncation_and_never_exceeds_budget(tmp_path: Path) -> None:
    api = backend()
    log = tmp_path / "x.jsonl"
    sink = api.InteractionSink(log, max_bytes=1024)
    for n in range(40):
        sink.append(row(n))
    sink.close()
    result = api.read_interactions(log)
    assert result["truncated"] is True
    assert {k: v for k, v in result["events"][-1].items() if k != "at"} == {
        "type": "truncated",
        "v": 1,
        "reason": "capacity",
    }
    assert 1e12 <= result["events"][-1]["at"] <= 4e12
    assert log.with_suffix(".interactions.jsonl").stat().st_size <= 1024


def test_refuses_symlink_and_nonregular_sink_and_reader(tmp_path: Path) -> None:
    api = backend()
    log = tmp_path / "x.jsonl"
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.write_text("secret")
    path = log.with_suffix(".interactions.jsonl")
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises((ValueError, OSError)):
        api.InteractionSink(log)
    with pytest.raises((ValueError, OSError)):
        api.read_interactions(log)
    assert outside.read_text() == "secret"


def test_refuses_nonregular_sink(tmp_path: Path) -> None:
    api = backend()
    log = tmp_path / "x.jsonl"
    log.with_suffix(".interactions.jsonl").mkdir()
    with pytest.raises((ValueError, OSError)):
        api.InteractionSink(log)


def test_reader_rejects_invalid_rows_bounds_lines_and_retains_partial(tmp_path: Path) -> None:
    api = backend()
    log = tmp_path / "x.jsonl"
    path = log.with_suffix(".interactions.jsonl")
    good = json.dumps(row()).encode() + b"\n"
    path.write_bytes(b"x" * 10000 + b"\n" + b'{"type":"unknown","page_text":"text"}\n' + good + good[:-2])
    result = api.read_interactions(log)
    assert result["events"] == [row()]
    assert result["truncated"] and not result["complete"]
    assert result["next_cursor"] == path.stat().st_size - len(good[:-2])
    with path.open("ab") as stream:
        stream.write(b"}\n")
    resumed = api.read_interactions(log, cursor=result["next_cursor"])
    assert resumed["events"] == [row()] and resumed["complete"]


@pytest.mark.parametrize(
    "kwargs", [{"cursor": -1}, {"cursor": True}, {"cursor": 0.5}, {"limit": 0}, {"limit": 5001}, {"limit": True}]
)
def test_reader_rejects_invalid_pagination(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        backend().read_interactions(tmp_path / "x.jsonl", **kwargs)


def test_reader_rejects_deep_json_without_failing_the_page(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    path = log.with_suffix(".interactions.jsonl")
    path.write_bytes(b"[" * 1200 + b"0" + b"]" * 1200 + b"\n" + json.dumps(row()).encode() + b"\n")
    result = backend().read_interactions(log)
    assert result["events"] == [row()] and result["truncated"]


def test_read_window_does_not_cut_and_lose_a_valid_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = backend()
    log = tmp_path / "x.jsonl"
    first = json.dumps(row(1)).encode() + b"\n"
    second = json.dumps(row(2)).encode() + b"\n"
    log.with_suffix(".interactions.jsonl").write_bytes(first + second)
    monkeypatch.setattr(api, "READ_WINDOW", len(first) + len(second) // 2)
    result = api.read_interactions(log)
    assert result["events"] == [row(1)]
    assert result["next_cursor"] == len(first)
    resumed = api.read_interactions(log, cursor=result["next_cursor"])
    assert resumed["events"] == [row(2)] and resumed["complete"]
