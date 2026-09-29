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

from octowright import defaults, personas, runner_video, sequences
from octowright import macros as macro_mod
from octowright._paths import reject_unsafe_path
from octowright.mcp_types import TestSuiteCaseResult, TestSuiteResult

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
    kind: str = "webkit",
    tag: str | None = None,
    out_path: str | None = None,
    pool: Any,
    max_parallel: int = 1,
    persona: str | None = None,
    redact_errors: bool = False,
    artifacts: Path | None = None,
    videos: list[Path] | None = None,
) -> TestSuiteResult:
    """Discover test macros, run each in an ephemeral browser, collect results, write JUnit XML.

    Discovery uses the global MACROS_DIR from octowright.macros.storage; override
    that via the OCTOWRIGHT_MACROS_DIR env var if you need a different directory.

    *videos*, when given, records every test's browser and appends each
    finalised video to it as that test's browser closes (completion order), even
    when the test fails. With *artifacts* they are copied there as
    ``<macro>.webm`` -- see ``octowright.runner_video``. *artifacts* is used
    for nothing else here.
    """
    if max_parallel < 1:
        raise ValueError("max_parallel must be >= 1")
    video_root = runner_video.artifacts_root(artifacts) if videos is not None else None

    entries = macro_mod.list_macros()
    tests: list[dict[str, Any]] = []
    for entry in entries:
        try:
            full = macro_mod.load_macro(entry["name"])
        except FileNotFoundError:
            continue
        if _is_test(full, tag):
            tests.append(full)

    async def _run_test(t: dict[str, Any]) -> TestSuiteCaseResult:
        start = datetime.now(UTC)
        iid: str | None = None
        ok = True
        err: str | None = None
        teardown_warning: str | None = None
        watch: runner_video.RunVideos | None = None
        try:
            # Tests start on about:blank so they don't accidentally depend on the global
            # DEFAULT_URL (which points at the production site and is CSP-locked).
            # Macros that need a specific URL should issue `navigate` as their first action.
            launch_result = await pool.launch(
                kind=kind,
                url="about:blank",
                headed=False,
                label=f"test-{t['name']}",
                viewport_w=defaults.DEFAULT_VIEWPORT_W,
                viewport_h=defaults.DEFAULT_VIEWPORT_H,
                profile=persona,
                **runner_video.launch_kwargs(videos),
            )
            iid = launch_result["instance_id"]
            session = pool.get(iid)
            if videos is not None:
                watch = await runner_video.RunVideos.watch(session)
            await macro_mod.run_macro(session=session, name=t["name"], args={})
        except Exception as e:
            ok = False
            err = redact_error(e) if redact_errors else repr(e)
        finally:
            if iid is not None:
                try:
                    await pool.close(iid, force=True)
                except Exception as e:
                    close_err = repr(e)
                    if ok:
                        # The test itself passed; record the teardown failure
                        # as a warning but don't flip ok to False.
                        teardown_warning = close_err
                        log.warning(
                            "octowright.runner.teardown_failed",
                            test=t["name"],
                            instance_id=iid,
                            error=close_err,
                        )
                    else:
                        # Test already failed — append close failure to the
                        # primary error so the JUnit report carries both.
                        err = f"{err}; close failed: {close_err}" if err else close_err
            if watch is not None and videos is not None:
                videos.extend(await watch.finalise(artifacts=video_root, stem=t["name"]))
        duration = (datetime.now(UTC) - start).total_seconds()
        result: TestSuiteCaseResult = {
            "name": t["name"],
            "ok": ok,
            "error": err,
            "duration": duration,
        }
        if teardown_warning is not None:
            result["teardown_warning"] = teardown_warning
        return result

    semaphore = asyncio.Semaphore(max_parallel)

    async def _run_bounded(t: dict[str, Any]) -> TestSuiteCaseResult:
        async with semaphore:
            return await _run_test(t)

    results = list(await asyncio.gather(*(_run_bounded(t) for t in tests)))

    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed

    report_path = Path(out_path) if out_path else _default_report_path()
    report_path = reject_unsafe_path(report_path, defaults.RECORDINGS_DIR, label="suite report path")
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


def redact_error(exc: BaseException) -> str:
    """A failure line that carries no exception text.

    octowright's macro failures raise ``RuntimeError(payload)``; the payload
    names the macro, the step and the action, which is enough to find the fault
    and is never derived from page content or arguments. Anything else is
    reduced to its type, because an exception message can quote a typed value.
    """
    payload = exc.args[0] if exc.args else None
    if isinstance(payload, dict) and "macro" in payload and "failed_at_step" in payload:
        action = payload.get("failed_action") or {}
        kind = action.get("action", "?") if isinstance(action, dict) else "?"
        return f"macro {payload['macro']} failed at step {payload['failed_at_step']} ({kind})"
    return type(exc).__name__


async def run_sequence_file(
    *,
    sequence: Path,
    kind: str,
    persona: str | None,
    artifacts: Path | None,
    redact_errors: bool,
    out_path: str | None,
    pool: Any,
    videos: list[Path] | None = None,
) -> TestSuiteResult:
    """Run a macro sequence file in one browser of *persona*, as a test suite.

    The same walk as ``macro_run_sequence`` with ``stop_on_failure=True``, kept
    as its own loop because that call raises on a failure and discards the
    steps that had already passed, which the JUnit report needs.

    Every reference is resolved before anything launches, so a sequence naming a
    credential its persona cannot supply fails without a browser. The sequence
    stops at the first failing macro; later steps are reported skipped, never
    as passed or as failures of their own. One JUnit testcase per step.

    *videos*, when given, records the browser and receives the finalised video
    paths after it closes -- in a ``finally``, so a failed or interrupted run
    still leaves them. With *artifacts* they are copied there as
    ``<sequence-stem>.webm`` (later pages ``<stem>-2.webm``, ...); see
    ``octowright.runner_video``.
    """
    steps = sequences.load_sequence(Path(sequence))
    persona_obj = personas.load_persona(persona) if persona else None
    names, args_list = sequences.resolve_steps(steps, persona=persona_obj, artifacts=artifacts)
    if artifacts is not None:
        Path(artifacts).mkdir(parents=True, exist_ok=True)

    results: list[TestSuiteCaseResult] = []
    launched = await pool.launch(
        kind=kind,
        url="about:blank",
        headed=False,
        label=f"sequence-{Path(sequence).stem}",
        viewport_w=defaults.DEFAULT_VIEWPORT_W,
        viewport_h=defaults.DEFAULT_VIEWPORT_H,
        profile=persona,
        **runner_video.launch_kwargs(videos),
    )
    iid = launched["instance_id"]
    watch: runner_video.RunVideos | None = None
    try:
        session = pool.get(iid)
        if videos is not None:
            watch = await runner_video.RunVideos.watch(session)
        failed = False
        for name, args in zip(names, args_list, strict=True):
            if failed:
                results.append({"name": name, "ok": False, "error": "not run", "duration": 0.0, "skipped": True})
                continue
            start = datetime.now(UTC)
            try:
                await macro_mod.run_macro(session=session, name=name, args=args)
                results.append({"name": name, "ok": True, "error": None, "duration": _since(start)})
            except Exception as exc:
                failed = True
                error = redact_error(exc) if redact_errors else str(exc)
                results.append({"name": name, "ok": False, "error": error, "duration": _since(start)})
    finally:
        await _close_and_collect(pool, iid, watch, videos, artifacts=artifacts, stem=Path(sequence).stem)

    passed = sum(1 for r in results if r["ok"])
    report_path = reject_unsafe_path(
        _sequence_report_path(out_path, artifacts), defaults.RECORDINGS_DIR, label="suite report path"
    )
    _write_junit(results, report_path, kind=kind)
    log.info("octowright.runner.sequence_finished", total=len(results), passed=passed, report=str(report_path))
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "report_path": str(report_path),
        "results": results,
    }


async def _close_and_collect(
    pool: Any,
    iid: str,
    watch: runner_video.RunVideos | None,
    videos: list[Path] | None,
    *,
    artifacts: Path | None,
    stem: str,
) -> None:
    """Close the browser, then -- even if the close raised -- collect its videos.

    Playwright finishes writing a video only when its context closes, so the
    collection must follow the close; it must also survive a close that
    failed, because a run that went wrong is the one whose video is wanted.
    """
    try:
        await pool.close(iid, force=True)
    finally:
        if watch is not None and videos is not None:
            videos.extend(await watch.finalise(artifacts=artifacts, stem=stem))


def _since(start: datetime) -> float:
    return (datetime.now(UTC) - start).total_seconds()


def _sequence_report_path(out_path: str | None, artifacts: Path | None) -> Path:
    """``--out`` if given, else beside the artifacts, else the suite default.

    The evidence and its report stay together, under the recordings root.
    """
    if out_path:
        return Path(out_path)
    if artifacts is not None:
        return Path(artifacts) / "octowright-report.xml"
    return _default_report_path()


def _default_report_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path.cwd() / f"octowright-report-{stamp}.xml"


def _write_junit(results: list[TestSuiteCaseResult], path: Path, *, kind: str) -> None:
    suite = ET.Element(
        "testsuite",
        {
            "name": "octowright",
            "tests": str(len(results)),
            "failures": str(sum(1 for r in results if not r["ok"] and not r.get("skipped"))),
            "skipped": str(sum(1 for r in results if r.get("skipped"))),
            "time": str(sum(r["duration"] for r in results)),
        },
    )
    for r in results:
        _junit_case(suite, r, kind=kind)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def _junit_case(suite: ET.Element, r: TestSuiteCaseResult, *, kind: str) -> None:
    case = ET.SubElement(
        suite,
        "testcase",
        {
            "classname": f"octowright.{kind}",
            "name": r["name"],
            "time": str(r["duration"]),
        },
    )
    if r.get("skipped"):
        ET.SubElement(case, "skipped", {"message": r["error"] or "not run"})
    elif not r["ok"]:
        fail = ET.SubElement(case, "failure", {"message": r["error"] or "failed"})
        fail.text = r["error"] or "failed"
