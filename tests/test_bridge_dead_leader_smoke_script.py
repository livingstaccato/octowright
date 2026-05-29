# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


def _load_script() -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts" / "bridge_dead_leader_smoke.py"
    spec = importlib.util.spec_from_file_location("bridge_dead_leader_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_env_isolates_octowright_paths(tmp_path: Path) -> None:
    script = _load_script()

    env = script.smoke_env(tmp_path, 45678)

    assert env["OCTOWRIGHT_HTTP_PORT"] == "45678"
    assert env["OCTOWRIGHT_HEADLESS"] == "1"
    assert env["OCTOWRIGHT_LOCK_PATH"] == str(tmp_path / "state" / "octowright.lock")
    assert env["OCTOWRIGHT_BRIDGE_STATE"] == str(tmp_path / "state" / "bridge-state.json")
    assert env["OCTOWRIGHT_BRIDGE_HEALTH_INTERVAL_SECONDS"] == "0.5"
    assert env["OCTOWRIGHT_BRIDGE_HEALTH_MAX_FAILURES"] == "2"
    assert env["OCTOWRIGHT_BRIDGE_REQUEST_TIMEOUT_SECONDS"] == "2"
    assert env["OCTOWRIGHT_PROFILE"] == "core"


def test_status_from_text_requires_object_payload() -> None:
    script = _load_script()

    assert script.status_from_text('{"daemon": {"pid": 123}}') == {"daemon": {"pid": 123}}

    try:
        script.status_from_text("[]")
    except RuntimeError as exc:
        assert "non-object" in str(exc)
    else:
        raise AssertionError("status_from_text should reject non-object JSON")


def test_read_lock_pid_handles_missing_bad_and_good_files(tmp_path: Path) -> None:
    script = _load_script()
    path = tmp_path / "octowright.lock"

    assert script.read_lock_pid(path) is None
    path.write_text("{bad", encoding="utf-8")
    assert script.read_lock_pid(path) is None
    path.write_text(json.dumps({"pid": "not-int"}), encoding="utf-8")
    assert script.read_lock_pid(path) is None
    path.write_text(json.dumps({"pid": 321}), encoding="utf-8")
    assert script.read_lock_pid(path) == 321


def test_parse_args_defaults() -> None:
    script = _load_script()

    args = script.parse_args([])

    assert args.command == ".venv/bin/octowright"
    assert args.cwd == "."
    assert args.port == 0
    assert args.timeout == 45.0
    assert args.keep_daemon is False
