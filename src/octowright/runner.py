# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright import macros as macro_mod

log = get_logger(__name__)


def _is_test(macro: dict[str, Any], tag: str | None) -> bool:
    """Return True if the macro qualifies as a test.

    A macro is a test if its description starts with ``[test]`` (bare) or
    ``[test:sometag]`` (tagged).  When *tag* is given, only macros whose tag
    matches are included.
    """
    desc = macro.get("description") or ""
    m = re.match(r"^\[test(?::([^\]]+))?\]", desc)
    if not m:
        return False
    if tag is None:
        return True
    return m.group(1) == tag


async def run_suite(
    *,
    macros_dir: str | None,  # noqa: ARG001 — reserved for per-suite dir override; default MACROS_DIR used today
    kind: str = "webkit",
    tag: str | None = None,
    out_path: str | None = None,
    pool: Any,
    max_parallel: int = 1,
) -> dict[str, Any]:
    """Discover test macros, run each in an ephemeral browser, collect results, write JUnit XML.

    *macros_dir* is accepted for API completeness (future: filter to a subdir).
    Currently discovery always uses the global MACROS_DIR from octowright.macros.storage.
    """
    if max_parallel < 1:
        raise ValueError("max_parallel must be >= 1")

    entries = macro_mod.list_macros()
    tests: list[dict[str, Any]] = []
    for entry in entries:
        try:
            full = macro_mod.load_macro(entry["name"])
        except FileNotFoundError:
            continue
        if _is_test(full, tag):
            tests.append(full)

    async def _run_test(t: dict[str, Any]) -> dict[str, Any]:
        start = datetime.now(UTC)
        iid: str | None = None
        ok = True
        err: str | None = None
        try:
            # Tests start on about:blank so they don't accidentally depend on the global
            # DEFAULT_URL (which points at the production site and is CSP-locked).
            # Macros that need a specific URL should issue `navigate` as their first action.
            launch_result = await pool.launch(
                kind=kind,
                url="about:blank",
                headed=False,
                label=f"test-{t['name']}",
                viewport_w=1280,
                viewport_h=800,
                profile=None,
            )
            iid = launch_result["instance_id"]
            session = pool.get(iid)
            await macro_mod.run_macro(session=session, name=t["name"], args={})
        except Exception as e:
            ok = False
            err = repr(e)
        finally:
            if iid is not None:
                try:
                    await pool.close(iid)
                except Exception as e:
                    ok = False
                    close_err = repr(e)
                    err = f"{err}; close failed: {close_err}" if err else close_err
        duration = (datetime.now(UTC) - start).total_seconds()
        return {
            "name": t["name"],
            "ok": ok,
            "error": err,
            "duration": duration,
        }

    semaphore = asyncio.Semaphore(max_parallel)

    async def _run_bounded(t: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _run_test(t)

    results = list(await asyncio.gather(*(_run_bounded(t) for t in tests)))

    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed

    report_path = Path(out_path) if out_path else _default_report_path()
    _write_junit(results, report_path, kind=kind)

    log.info(
        "octowright.runner.finished",
        total=len(results),
        passed=passed,
        failed=failed,
        report=str(report_path),
    )
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "report_path": str(report_path),
        "results": results,
    }


def _default_report_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path.cwd() / f"octowright-report-{stamp}.xml"


def _write_junit(results: list[dict[str, Any]], path: Path, *, kind: str) -> None:
    suite = ET.Element(
        "testsuite",
        {
            "name": "octowright",
            "tests": str(len(results)),
            "failures": str(sum(1 for r in results if not r["ok"])),
            "time": str(sum(r["duration"] for r in results)),
        },
    )
    for r in results:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"octowright.{kind}",
                "name": r["name"],
                "time": str(r["duration"]),
            },
        )
        if not r["ok"]:
            fail = ET.SubElement(case, "failure", {"message": r["error"] or "failed"})
            fail.text = r["error"] or "failed"
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
