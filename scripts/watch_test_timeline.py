# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Name the test that was running when a browser died.

The recurring headed-Chromium abort (``EXC_BREAKPOINT``/``SIGTRAP`` on
``CrBrowserMain``, see ``scripts/characterize_headed_crash.py``) has resisted
every synthetic reproduction: launch concurrency plus churn produced **0
crashes in ~2,150 headed launches** across both the raw-Playwright and the real
``BrowserPool`` arms, while an ordinary ``make test`` on the same machine and
build killed a browser the same afternoon. So the trigger is something the
suite does and the storm does not -- and the one fact nobody has ever had is
*which test was running at the moment of the crash*.

The instrumentation for that already half-exists. ``tests/conftest.py`` writes
``<phase> <nodeid>`` to ``.pytest-current-test`` at the start of every
setup/call/teardown, because a pytest-timeout kill prints thread stacks and no
test name. But it **overwrites** that file each phase and deletes it at session
end, so it answers "what is running now" and never "what was running then".

This records the history that file throws away, and then reads it back against
a macOS crash report. It is deliberately a separate process that only ever
*reads* that file: the alternative -- appending from ``conftest`` -- puts I/O
in the hot path of every phase of every test, on a suite where the timing
bounds are themselves load-bearing.

Sampling rather than watching is the same trade. A 0.25s poll costs nothing and
cannot wedge the run; the risk is missing a phase shorter than the interval,
which for correlating a crash to within a second or two does not matter.

Usage::

    # in one shell, before starting the suite
    uv run --active python scripts/watch_test_timeline.py

    # afterwards, given a crash report (--newest-crash finds the latest one)
    uv run --active python scripts/watch_test_timeline.py --correlate --newest-crash

    # or an explicit report, or any timestamp the log covers
    uv run --active python scripts/watch_test_timeline.py --correlate <path-to.ips>
    uv run --active python scripts/watch_test_timeline.py --correlate 2026-09-07T10:42:17
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Both of these are defined once, elsewhere, and imported rather than spelled
# again here: two copies of a path is how they drift apart.
from octowright.browser_pool.crash_reports import DEFAULT_REPORTS_DIR  # noqa: E402
from tests.breadcrumb import CURRENT_TEST_BREADCRUMB  # noqa: E402

DEFAULT_LOG = ROOT / ".pytest-test-timeline.log"

# Mechanisms a test uses to crash a browser ON PURPOSE. A crash correlated to
# such a test is expected output rather than a defect, and saying so is the
# difference between a five-second read and an afternoon.
#
# They are not a rounding error in the noise -- they ARE the noise. Measured on
# the machine where this was written: of 31 crash reports, 27 were
# EXC_BREAKPOINT on Chrome_ChildIOThread (a child aborting when
# ``test_stability_chaos_live`` kills the shared driver out from under it) and
# 3 were EXC_BAD_ACCESS on CrRendererMain (its CDP ``Page.crash``), leaving
# exactly ONE report of the real headed CrBrowserMain abort. Anyone reaching for
# ``--newest-crash`` after a suite run therefore gets a manufactured crash, and
# the signal is buried 30:1.
#
# Derived by scanning the correlated module rather than listing test names, so a
# chaos test added later is covered without editing this file -- the same reason
# ``macros/runtime`` derives RECORDER_NOISE instead of mirroring it by hand.
#
# KNOWN LIMITATION, and it errs in the direction that matters: matching is by
# substring, so a module that merely *mentions* a mechanism -- this script's own
# tests, a docstring explaining the noise -- is flagged as if it caused a crash.
# Telling "uses" from "mentions" needs an AST walk, and `Page.crash` is a string
# literal even in real use (it is sent over CDP), so no cheap rule separates
# them. This is a hint on a diagnostic line, not a verdict: read the note as
# "check whether this was deliberate", never as proof that it was.
_DELIBERATE_CRASH_MARKERS = ("Page.crash", "_pw.stop()")
_DELIBERATE_NOTE = "  [crashes browsers on purpose]"

#: The phases `tests/conftest.py` writes ahead of the nodeid.
_PHASES = ("setup", "call", "teardown")


def induces_deliberate_crashes(module_path: str) -> bool:
    """Whether this test module crashes a browser deliberately."""
    try:
        text = (ROOT / module_path).read_text(encoding="utf-8")
    except OSError:
        # Not a readable path (a synthetic nodeid, a moved file). Saying "not
        # deliberate" only costs a missing note; guessing the other way would
        # dismiss a real crash as expected.
        return False
    return any(marker in text for marker in _DELIBERATE_CRASH_MARKERS)


def annotate(nodeid: str) -> str:
    """``nodeid`` plus a note when its module manufactures crashes."""
    # A row is "<phase> <path>::<test>". Strip a KNOWN phase word rather than
    # splitting on a space: rpartition took what followed the LAST space, which
    # silently assumes no space in the path -- "call tests/my test.py::x"
    # resolved to "test.py", the read failed, and the row went unannotated with
    # no sign anything was wrong. A branch, but a correct one.
    phase, _, rest = nodeid.partition(" ")
    module = (rest if phase in _PHASES and rest else nodeid).split("::", 1)[0]
    return nodeid + (_DELIBERATE_NOTE if induces_deliberate_crashes(module) else "")


# Browser processes whose crash reports are worth correlating. Matches the
# spirit of crash_reports._BROWSER_TOKENS without importing a private name.
_BROWSER_TOKENS_RE = re.compile(r"chrome|chromium|firefox|webkit|playwright", re.IGNORECASE)

POLL_SECONDS = 0.25
# How far either side of a crash to show. A browser dies during the phase that
# provoked it, but the report's clock and ours can differ by a beat, and the
# provoking action may be in the phase before.
CORRELATE_WINDOW_SECONDS = 20.0

_STAMP = "%Y-%m-%dT%H:%M:%S"


def _now() -> str:
    return datetime.now().strftime(_STAMP)


def follow(log_path: Path) -> int:
    """Append every change of the current-test file, with a timestamp."""
    print(f"recording to {log_path} (ctrl-c to stop)", flush=True)
    previous = ""
    with log_path.open("a", encoding="utf-8") as handle:
        while True:
            try:
                current = CURRENT_TEST_BREADCRUMB.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                # No session running, or mid-write. Both are ordinary.
                current = ""
            if current and current != previous:
                handle.write(f"{_now()} {current}\n")
                handle.flush()  # a crash may kill this process too
                previous = current
            time.sleep(POLL_SECONDS)


def crash_reports_available() -> bool:
    """Whether this machine has the crash-report format the tool can read.

    Recording the timeline works anywhere -- it reads a file pytest writes. The
    CORRELATION half is macOS-only: it parses ``.ips`` reports from
    DiagnosticReports. On Windows the equivalent is a WER minidump under
    ``%LOCALAPPDATA%\\CrashDumps`` in an entirely different format, and on Linux
    it depends on the distro's core-dump handler; neither is implemented.

    Stated rather than left to fail quietly, because a glob on a directory that
    does not exist returns an EMPTY LIST rather than raising -- so without this
    the tool would answer "no browser crash reports" on Windows, which reads as
    "you have no crashes" instead of "this cannot see them".
    """
    return sys.platform == "darwin"


def newest_crash_report(thread: str | None = None) -> Path | None:
    """The most recently CRASHED browser report, or None.

    Ranked by the timestamp inside the report, not by file mtime: macOS
    rewrites these files (observed picking a report whose mtime was minutes old
    and whose crash was the previous day), so mtime answers "last touched"
    where the question is "last crashed".

    ``thread`` restricts the choice to reports whose faulting thread contains
    that substring, and it is the difference between this being useful and
    actively misleading. The directory is dominated by crashes the suite
    manufactures on purpose -- measured at 27 ``Chrome_ChildIOThread`` aborts
    and 3 ``CrRendererMain`` segfaults against ONE real ``CrBrowserMain`` -- so
    picking the newest blind hands you noise after any suite run.

    Deliberately a class filter and NOT a "skip the manufactured ones" filter:
    the chaos tests crash a real renderer, so their reports are byte-identical
    in signature to a genuine renderer crash. Signature cannot say what was
    deliberate -- only correlating against the timeline can, which is what the
    annotation on those rows is for. This says "show me the class I am hunting".
    """
    try:
        reports = [p for p in DEFAULT_REPORTS_DIR.glob("*.ips") if _BROWSER_TOKENS_RE.search(p.name)]
    except OSError:
        return None
    dated: list[tuple[datetime, Path]] = []
    for report in reports:
        if thread is not None:
            signature = crash_signature(report)
            if signature is None or thread.lower() not in signature[3].lower():
                continue
        try:
            dated.append((_crash_time(str(report)), report))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue  # a report we cannot date cannot be ranked
    if not dated:
        return None
    return max(dated, key=lambda pair: pair[0])[1]


def crash_signature(path: Path) -> tuple[str, str, str, str] | None:
    """``(process, version, exception, faulting thread)``, or None if unreadable."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        head, _, body = raw.partition("\n")
        meta, report = json.loads(head), json.loads(body)
    except (OSError, ValueError):
        return None
    index = report.get("faultingThread")
    threads = report.get("threads") or []
    thread = (
        threads[index].get("name") or threads[index].get("queue") or "?"
        if isinstance(index, int) and index < len(threads)
        else "?"
    )
    return (
        str(report.get("procName", "?")),
        str(meta.get("app_version") or "?"),
        str((report.get("exception") or {}).get("type", "?")),
        str(thread),
    )


def describe_crash(path: Path) -> str:
    """One line naming WHICH crash class a report is.

    Two very different failures land in this directory and conflating them
    wastes hours: a renderer ``EXC_BAD_ACCESS``/``SIGSEGV`` (the ordinary kind
    octowright already recovers from via ``page.on("crash")``) and the
    main-browser-process ``EXC_BREAKPOINT``/``SIGTRAP`` abort on
    ``CrBrowserMain`` that is actually under investigation. The frames are
    usually nearest-symbol noise from a stripped build, so the process, the
    exception type and the faulting thread are the signal.
    """
    signature = crash_signature(path)
    if signature is None:
        return "(unreadable crash report)"
    process, version, exception, thread = signature
    return f"{process} {version} -- {exception} on {thread}"


def _crash_time(target: str) -> datetime:
    """The moment of a crash, from an .ips path or a literal timestamp."""
    path = Path(target).expanduser()
    if path.is_file():
        head = path.read_text(encoding="utf-8", errors="replace").partition("\n")[0]
        stamp = json.loads(head)["timestamp"]
        # "2026-09-07 10:42:17.00 -0700" -> drop fraction and offset.
        return datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S")
    return datetime.strptime(target.strip()[:19], _STAMP)


def correlate(log_path: Path, target: str) -> int:
    when = _crash_time(target)
    if not log_path.exists():
        print(f"no timeline at {log_path}; run this script alongside the suite first")
        return 1

    rows: list[tuple[datetime, str]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        stamp, _, nodeid = line.partition(" ")
        try:
            rows.append((datetime.strptime(stamp, _STAMP), nodeid))
        except ValueError:
            continue
    if not rows:
        print(f"timeline at {log_path} has no usable rows")
        return 1

    print(f"crash at {when:{_STAMP}}; timeline covers {rows[0][0]:{_STAMP}} .. {rows[-1][0]:{_STAMP}}\n")
    hits = [(t, nodeid) for t, nodeid in rows if abs((t - when).total_seconds()) <= CORRELATE_WINDOW_SECONDS]
    if not hits:
        # The nearest row before the crash is still the best answer available.
        before = [row for row in rows if row[0] <= when]
        if not before:
            print("no timeline entry at or before the crash")
            return 1
        stamp, nodeid = before[-1]
        gap = (when - stamp).total_seconds()
        print(f"nothing within {CORRELATE_WINDOW_SECONDS:.0f}s; last test before the crash ({gap:.0f}s earlier):")
        print(f"  {stamp:{_STAMP}}  {annotate(nodeid)}")
        return 0

    for stamp, nodeid in hits:
        offset = (stamp - when).total_seconds()
        marker = "<-- crash" if offset >= 0 and stamp == min(t for t, _ in hits if t >= when) else ""
        print(f"  {stamp:{_STAMP}}  {offset:+6.0f}s  {annotate(nodeid)} {marker}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument(
        "--correlate",
        nargs="?",
        const="",
        metavar="IPS_OR_TIMESTAMP",
        help="Path to a crash report, or an ISO timestamp. Bare, with --newest-crash.",
    )
    parser.add_argument(
        "--newest-crash",
        action="store_true",
        help="Correlate against the most recent browser crash report on this machine.",
    )
    parser.add_argument(
        "--crash-thread",
        metavar="SUBSTR",
        help=(
            "Restrict --newest-crash to reports whose faulting thread matches, "
            "e.g. CrBrowserMain. The directory is dominated by crashes the suite "
            "manufactures on purpose, so the newest one is usually not the one "
            "you are hunting."
        ),
    )
    opts = parser.parse_args()
    if opts.crash_thread and not opts.newest_crash:
        # Accepting a flag that does nothing is the defect this release refuses
        # in LaunchOptions; without this the tool fell through to the recorder
        # and looked like it had hung.
        parser.error("--crash-thread only applies to --newest-crash")
    if (opts.newest_crash or opts.correlate) and not crash_reports_available():
        print(
            f"crash correlation is macOS-only (reads .ips reports); this is {sys.platform}.\n"
            "Recording the timeline still works -- run without --correlate."
        )
        return 1
    if opts.newest_crash:
        report = newest_crash_report(thread=opts.crash_thread)
        if report is None:
            scoped = f" with a faulting thread matching {opts.crash_thread!r}" if opts.crash_thread else ""
            print(f"no browser crash reports under {DEFAULT_REPORTS_DIR}{scoped}")
            return 1
        print(f"newest crash report: {report.name}")
        print(f"  {describe_crash(report)}")
        return correlate(opts.log, str(report))
    if opts.correlate:
        return correlate(opts.log, opts.correlate)
    try:
        return follow(opts.log)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
