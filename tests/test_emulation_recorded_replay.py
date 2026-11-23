# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Recorded emulation actions survive macro replay and complete script export."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from octowright.export import export_script
from octowright.macros.lint import lint_macro
from octowright.macros.runtime import _dispatch_standard
from tests.test_emulation_primitives import _FakeRoute, _session
from tests.test_expect_js_equality import _TS_ONLY


@pytest.mark.asyncio
async def test_recorded_abort_route_replays_through_the_macro_dispatcher(tmp_path: Path) -> None:
    recorded = _session(tmp_path / "original")
    await recorded.abort_route("**/api/refused", error_code="connectionreset")
    row = json.loads(recorded.log_path.read_text(encoding="utf-8").splitlines()[-1])
    kwargs = {key: row[key] for key in ("pattern", "error_code")}
    replayed = _session(tmp_path / "replayed")

    result = await _dispatch_standard(replayed, row["action"], kwargs, "abort_route")

    assert result == (1, 0)
    route = _FakeRoute()
    await replayed._active_routes["**/api/refused"](route)
    assert route.aborted == "connectionreset"


def test_recorded_abort_route_passes_macro_lint() -> None:
    issues = lint_macro(
        {
            "name": "abort-request",
            "actions": [{"action": "abort_route", "pattern": "**/api/refused", "error_code": "connectionreset"}],
        }
    )
    assert issues == []


@pytest.mark.asyncio
async def test_complete_typescript_export_parses_after_safe_area_is_set_and_cleared(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    session = _session(tmp_path)
    await session.set_safe_area(top=47, bottom=34)
    await session.set_safe_area()
    script = export_script(session.log_path, tmp_path / "replay.ts", fmt="ts")
    source = script.read_text(encoding="utf-8")
    # Node 20 cannot strip TypeScript. Remove only the header's known type
    # annotations; all emitted actions and their enclosing scopes stay intact.
    for annotation in (
        ": string",
        ": Browser | null",
        "!: BrowserContext",
        ': Page | ReturnType<Page["frameLocator"]> | NonNullable<ReturnType<Page["frame"]>>',
        "!: Page",
    ):
        source = source.replace(annotation, "")
    parsed_script = tmp_path / "replay.mjs"
    parsed_script.write_text(
        _TS_ONLY.sub(lambda m: ")" if m.group(0).startswith(")") else "", source), encoding="utf-8"
    )

    parsed = subprocess.run(
        [node, "--check", str(parsed_script)], capture_output=True, text=True, check=False, timeout=30
    )

    assert parsed.returncode == 0, parsed.stderr
