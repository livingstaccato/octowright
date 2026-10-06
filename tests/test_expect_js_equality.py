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
import time
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


# The helper's annotations, spelled out so plain node can run it. Only these
# shapes are removed, and the stripped text must parse -- a new annotation the
# list misses fails loudly as a SyntaxError rather than being skipped.
_TS_ONLY = re.compile(
    r": \[unknown, unknown\]|: unknown\b|\): boolean|: boolean\b| as unknown\[\]| as Record<string, unknown>"
)

#: Of the suite's per-test bound (``timeout`` in pyproject), what one node run
#: may take before it is called hung. This bounds a hang, not slowness: node's
#: start-up time is not what these tests pin, and a bound near its slow tail
#: failed them on runners that were only slow. A cold start took 3-11s on
#: healthy Windows legs, over 30s on a degraded one (2026-10-05, where the
#: next, warm, start took 13s too). Most of the per-test bound, so a hang
#: fails this test by name instead of tripping pytest-timeout, which ends the
#: whole run.
_HUNG_SHARE_OF_TEST_TIMEOUT = 0.8


def _run_ts(tmp_path: Path, source: str, per_test_timeout_s: float) -> Any:
    """Run *source* (the helper's TypeScript) on plain node, its annotations stripped.

    One node start per run: node's own type stripping is not used. It was
    probed with a node start of its own, and no node these tests meet takes
    it -- CI installs node 20, which rejects the flag ("bad option"), and a
    node built without TypeScript support raises ERR_NO_TYPESCRIPT -- so the
    probe doubled the node starts and decided nothing.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = tmp_path / "check.mjs"
    script.write_text(_TS_ONLY.sub(lambda m: ")" if m.group(0).startswith(")") else "", source), encoding="utf-8")
    hung_s = per_test_timeout_s * _HUNG_SHARE_OF_TEST_TIMEOUT if per_test_timeout_s > 0 else None
    started = time.monotonic()
    try:
        done = subprocess.run([node, str(script)], capture_output=True, text=True, check=False, timeout=hung_s)
    except subprocess.TimeoutExpired:
        pytest.fail(f"node did not exit within {hung_s:g}s ({time.monotonic() - started:.1f}s): it is hung, not slow")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture
def per_test_timeout_s(pytestconfig: pytest.Config) -> float:
    """The suite's per-test bound in seconds (``--timeout``, else the ini); 0 without pytest-timeout."""
    try:
        configured = pytestconfig.getoption("timeout", None) or pytestconfig.getini("timeout")
    except ValueError:  # both are pytest-timeout's, unknown without it
        return 0.0
    return float(configured or 0)


def test_ts_helper_agrees_with_python_equality(tmp_path: Path, per_test_timeout_s: float) -> None:
    verdicts = _run_ts(
        tmp_path,
        _TS_PY_EQUALS
        + f"\nconst cases = {json.dumps(CASES)};\n"
        + "console.log(JSON.stringify(cases.map(([a, b]: [unknown, unknown]) => pyEquals(a, b))));\n",
        per_test_timeout_s,
    )
    assert verdicts == [actual == expected for actual, expected in CASES]


def test_ts_helper_treats_a_js_undefined_as_python_none(tmp_path: Path, per_test_timeout_s: float) -> None:
    """Playwright-Python hands back ``{'a': None}`` for ``{a: undefined}`` on all
    three engines (measured); the TS side sees undefined, so it must equal null."""
    verdicts = _run_ts(
        tmp_path,
        _TS_PY_EQUALS + "\nconsole.log(JSON.stringify([pyEquals(undefined, null), pyEquals({a: undefined}, {a: null}),"
        " pyEquals({a: undefined}, {})]));\n",
        per_test_timeout_s,
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
