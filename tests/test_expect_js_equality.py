# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``expect_js equals`` means the same thing live, in both exports and in the macro CLI.

The session compares with Python ``!=``; the Python export and the macro CLI
run Python too. The TypeScript export compared ``JSON.stringify`` of both
sides, which is key-order and type sensitive: ``{"a":1,"b":2}`` against a page
returning ``{b:2,a:1}`` passed live and threw in TS. It now calls a helper
with Python ``==`` semantics on JSON-shaped values -- including Python's
``True == 1`` / ``False == 0``, which is pinned below rather than left to
whichever side a reader assumes.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.export_ts import _TS_PY_EQUALS, _ts_expect_js
from tests.macro_lint.test_cli_export_execution import _install, _Recorder

# (page value, recorded equals). The expected verdict is Python's own ==, so
# the table cannot drift from the semantics it is pinning.
CASES: list[tuple[Any, Any]] = [
    ({"a": 1, "b": 2}, {"b": 2, "a": 1}),  # key order does not matter
    ([1, 2], [2, 1]),  # list order does
    (True, 1),  # Python: True == 1
    (False, 0),
    (True, 1.0),
    (True, 2),
    ([True, {"x": False}], [1, {"x": 0}]),  # and nested
    (1, 1.0),
    ("1", 1),
    (None, None),
    (None, 0),
    (None, False),
    ({"a": None}, {}),
    ({"a": 1}, {"a": 1, "b": 2}),
    ([], {}),
    ("", False),
    ({"k": [1, {"deep": "v"}]}, {"k": [1, {"deep": "v"}]}),
]


# The helper's annotations, spelled out so a node built without TypeScript
# support (this one: ERR_NO_TYPESCRIPT) can still run it. Only these shapes are
# removed, and the stripped text must parse -- a new annotation the list
# misses fails loudly as a SyntaxError rather than being skipped.
_TS_ONLY = re.compile(r": \[unknown, unknown\]|: unknown\b|\): boolean|: boolean\b| as unknown\[\]| as Record<string, unknown>")


def _run_ts(tmp_path: Path, source: str) -> Any:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    probe = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings", "-e", "const x: number = 1"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if probe.returncode == 0:
        script, command = tmp_path / "check.ts", [node, "--experimental-strip-types", "--no-warnings"]
    else:
        source = _TS_ONLY.sub(lambda m: ")" if m.group(0).startswith(")") else "", source)
        script, command = tmp_path / "check.mjs", [node]
    script.write_text(source, encoding="utf-8")
    out = subprocess.run([*command, str(script)], capture_output=True, text=True, check=True, timeout=60).stdout
    return json.loads(out)


def test_ts_helper_agrees_with_python_equality(tmp_path: Path) -> None:
    verdicts = _run_ts(
        tmp_path,
        _TS_PY_EQUALS
        + f"\nconst cases = {json.dumps(CASES)};\n"
        + "console.log(JSON.stringify(cases.map(([a, b]: [unknown, unknown]) => pyEquals(a, b))));\n",
    )
    assert verdicts == [actual == expected for actual, expected in CASES]


def test_ts_helper_treats_a_js_undefined_as_python_none(tmp_path: Path) -> None:
    """Playwright-Python hands back ``{'a': None}`` for ``{a: undefined}`` on all
    three engines (measured); the TS side sees undefined, so it must equal null."""
    verdicts = _run_ts(
        tmp_path,
        _TS_PY_EQUALS
        + "\nconsole.log(JSON.stringify([pyEquals(undefined, null), pyEquals({a: undefined}, {a: null}),"
        " pyEquals({a: undefined}, {})]));\n",
    )
    assert verdicts == [True, True, False]


def test_ts_export_compares_through_the_helper() -> None:
    line = _ts_expect_js({"action": "expect_js", "expression": "window.state", "equals": {"a": 1}})
    assert "pyEquals(" in line
    assert "JSON.stringify" not in line


def _cli(monkeypatch: pytest.MonkeyPatch, action: dict[str, Any]) -> None:
    rec = _Recorder()
    _install(monkeypatch, rec)
    source = render_macro_cli(name="eq", macro={"parameters": [], "actions": [action]}, include_evidence=False)
    namespace: dict[str, Any] = {}
    exec(source, namespace)  # executing the generated artefact is the point
    asyncio.run(namespace["run_eq"](trusted_origins=()))


def test_macro_cli_treats_a_recorded_null_equals_as_a_truthy_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The session records ``equals=None`` for a truthy check, as replay reads it;
    the CLI keyed on the key's presence and demanded the page return null."""
    _cli(monkeypatch, {"action": "expect_js", "expression": "window.ok", "equals": None})


def test_macro_cli_pins_python_bool_int_equality(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fake page answers True; Python (and so live replay) says True == 1.
    _cli(monkeypatch, {"action": "expect_js", "expression": "window.ok", "equals": 1})
    with pytest.raises(RuntimeError, match="JS assertion failed"):
        _cli(monkeypatch, {"action": "expect_js", "expression": "window.ok", "equals": 2})
