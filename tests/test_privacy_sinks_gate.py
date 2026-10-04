# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The macro privacy surface's durable-write sinks are frozen (#248, Part 0).

`scripts/derive_privacy_sinks.py` finds every place macro execution, the
artifact store and the failure diagnostics write something that outlives the
call. Each one was reviewed for what it scrubs and frozen into
``tests/fixtures/privacy_sinks.json``. A sink added without updating the
fixture fails here, and so does a fixture row whose sink is gone: the
comparison is exact, in both directions, and a scan that finds nothing fails
rather than passing on an empty set.

Regenerate after review: ``uv run python scripts/derive_privacy_sinks.py --write``.
"""

from __future__ import annotations

import ast
import json
from typing import Any

from tests._script_module import load_script_module

_DERIVE = load_script_module("scripts/derive_privacy_sinks.py")


def _rows(rows: list[dict[str, str]]) -> set[tuple[str, str, str, str]]:
    return {(row["module"], row["function"], row["callee"], row["shape"]) for row in rows}


def _frozen() -> set[tuple[str, str, str, str]]:
    return _rows(json.loads(_DERIVE.FIXTURE.read_text(encoding="utf-8")))


def test_the_derived_sinks_are_exactly_the_frozen_ones() -> None:
    derived, frozen = _rows(_DERIVE.derive()), _frozen()

    assert derived, "the scan found no sinks at all: it is broken, not clean"
    assert frozen, "the fixture is empty"
    added = sorted(derived - frozen)
    gone = sorted(frozen - derived)
    assert added == [], f"new durable-write sinks; review what each scrubs, then regenerate the fixture: {added}"
    assert gone == [], f"frozen sinks that no longer exist; regenerate the fixture: {gone}"


def test_the_fixture_has_no_line_numbers_and_no_duplicates() -> None:
    rows: list[dict[str, Any]] = json.loads(_DERIVE.FIXTURE.read_text(encoding="utf-8"))
    assert all(set(row) == {"module", "function", "callee", "shape"} for row in rows)
    assert len(rows) == len(_rows(rows))


def _scan(source: str) -> set[tuple[str, str, str, str]]:
    scanner = _DERIVE._Scanner("pkg.mod")
    scanner.visit(ast.parse(source))
    return scanner.rows


def test_the_scanner_finds_each_kind_of_sink() -> None:
    rows = _scan(
        "class C:\n"
        "    def m(self, p, rec):\n"
        "        p.write_text('x')\n"
        "        rec.record('a')\n"
        "        atomic_write_text(p, 'x')\n"
        "        open(p, 'w').close()\n"
        "        p.open('a')\n"
        "        (p / 'x').write_bytes(b'')\n"
        "def f(path):\n"
        "    open(path).read()\n"
        "    _HISTOGRAM.record(1)\n"
    )
    assert rows == {
        ("pkg.mod", "C.m", "write_text", ".p"),
        ("pkg.mod", "C.m", "record", ".rec"),
        ("pkg.mod", "C.m", "atomic_write_text", "call"),
        ("pkg.mod", "C.m", "open", "open:w"),
        ("pkg.mod", "C.m", "open", "open:a"),
        ("pkg.mod", "C.m", "write_bytes", ".<expr>"),
    }
