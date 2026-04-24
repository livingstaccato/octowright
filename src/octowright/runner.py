from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from . import macros as macro_mod
from .defaults import DEFAULT_URL

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
) -> dict[str, Any]:
    """Discover test macros, run each in an ephemeral browser, collect results, write JUnit XML.

    *macros_dir* is accepted for API completeness (future: filter to a subdir).
    Currently discovery always uses the global MACROS_DIR from macros.py.
    """
    entries = macro_mod.list_macros()
    tests: list[dict[str, Any]] = []
    for entry in entries:
        try:
            full = macro_mod.load_macro(entry["name"])
        except FileNotFoundError:
            continue
        if _is_test(full, tag):
            tests.append(full)

    results: list[dict[str, Any]] = []
    for t in tests:
        start = datetime.now(UTC)
        launch_result = await pool.launch(
            kind=kind,
            url=DEFAULT_URL,
            headed=False,
            label=f"test-{t['name']}",
            viewport_w=1280,
            viewport_h=800,
            profile=None,
        )
        iid = launch_result["instance_id"]
        session = pool.get(iid)
        ok = True
        err: str | None = None
        try:
            await macro_mod.run_macro(session=session, name=t["name"], args={})
        except Exception as e:
            ok = False
            err = repr(e)
        finally:
            await pool.close(iid)
        duration = (datetime.now(UTC) - start).total_seconds()
        results.append(
            {
                "name": t["name"],
                "ok": ok,
                "error": err,
                "duration": duration,
            }
        )

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
