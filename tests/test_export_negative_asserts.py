# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""browser_export_script must not silently drop the negative assertions.

Both need state a linear script does not have (network listeners from the
start of the run, the drawn-text scan), and ``macro_export_cli`` already runs
them. A dropped check lets an exported security test pass on a leaking page,
so the exporters fail closed: the step raises and names the tool that runs it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.defaults import REDACTED_ASSERTION_TEXT
from octowright.export import export_script


def _export(tmp_path: Path, entries: list[dict[str, object]], fmt: str) -> str:
    log = tmp_path / "rec.jsonl"
    log.write_text("\n".join(json.dumps({"ts": "2026-09-24T10:00:00Z", **e}) for e in entries), encoding="utf-8")
    out = tmp_path / ("out.py" if fmt == "python" else "out.ts")
    return export_script(log, out, fmt=fmt).read_text(encoding="utf-8")


@pytest.mark.parametrize("fmt", ["python", "ts"])
@pytest.mark.parametrize(
    "entry",
    [
        {"action": "expect_network_clean"},
        {"action": "expect_no_text", "selector": "body", "text": REDACTED_ASSERTION_TEXT},
    ],
)
def test_the_assertion_fails_closed_and_names_the_tool(tmp_path: Path, fmt: str, entry: dict[str, object]) -> None:
    source = _export(tmp_path, [{"action": "navigate", "url": "https://x.test/"}, entry], fmt)
    assert str(entry["action"]) in source
    assert "macro_export_cli" in source
    assert ("raise RuntimeError" if fmt == "python" else "throw new Error") in source


@pytest.mark.parametrize("fmt", ["python", "ts"])
def test_the_mark_step_is_kept_as_a_comment(tmp_path: Path, fmt: str) -> None:
    source = _export(tmp_path, [{"action": "mark_network_clean"}], fmt)
    assert "mark_network_clean" in source


def test_the_python_export_still_compiles(tmp_path: Path) -> None:
    source = _export(tmp_path, [{"action": "expect_network_clean"}, {"action": "mark_network_clean"}], "python")
    compile(source, "out.py", "exec")
