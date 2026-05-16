# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for ``ci/build_test_matrix.py``.

The script lives outside the package so importing it requires adding the
``ci/`` directory to ``sys.path``. The script's logic is pure (filter by
os/arch over a static target list) so we test ``build_matrix`` directly
plus a smoke check of the script's stdout shape.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parent.parent / "ci"
_SCRIPT_PATH = _CI_DIR / "build_test_matrix.py"


def _load_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("build_test_matrix", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_matrix_all_returns_full_runner_list() -> None:
    mod = _load_module()
    result = mod.build_matrix("all", "all")
    assert {(row["os"], row["arch"]) for row in result["include"]} == {
        ("linux", "amd64"),
        ("linux", "arm64"),
        ("macos", "amd64"),
        ("macos", "arm64"),
        ("windows", "amd64"),
        ("windows", "arm64"),
    }


def test_build_matrix_filters_by_os() -> None:
    mod = _load_module()
    result = mod.build_matrix("windows", "all")
    assert all(row["os"] == "windows" for row in result["include"])
    assert {row["arch"] for row in result["include"]} == {"amd64", "arm64"}


def test_build_matrix_filters_by_arch() -> None:
    mod = _load_module()
    result = mod.build_matrix("all", "arm64")
    assert all(row["arch"] == "arm64" for row in result["include"])
    assert {row["os"] for row in result["include"]} == {"linux", "macos", "windows"}


def test_build_matrix_filters_by_os_and_arch() -> None:
    mod = _load_module()
    result = mod.build_matrix("macos", "amd64")
    assert result == {"include": [{"os": "macos", "arch": "amd64", "runner": "macos-15-intel"}]}


def test_build_matrix_empty_when_no_target_matches() -> None:
    mod = _load_module()
    assert mod.build_matrix("plan9", "all") == {"include": []}


def test_script_stdout_matches_github_output_contract() -> None:
    """The script must emit a single ``matrix=<json>`` line on stdout so the
    workflow step can redirect it into ``$GITHUB_OUTPUT``. JSON must be the
    compact form (no spaces after separators) — GitHub treats multi-line
    values specially and a wide JSON object would break the contract.
    """
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        check=True,
        env={"TARGET_OS": "linux", "TARGET_ARCH": "amd64", "PATH": ""},
    )
    stdout = proc.stdout.strip()
    assert stdout.startswith("matrix=")
    payload = json.loads(stdout[len("matrix=") :])
    assert payload == {"include": [{"os": "linux", "arch": "amd64", "runner": "ubuntu-24.04"}]}
    # Compact separators: no spaces inside the JSON.
    assert " " not in stdout[len("matrix=") :]
