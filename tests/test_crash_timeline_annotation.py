# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A crash the suite caused on purpose must say so.

`scripts/watch_test_timeline.py` exists to name the test that was running when
a browser died. It answered that question correctly and still sent the reader
the wrong way, because `--newest-crash` picks the most recent report blind and
the report directory is dominated by crashes the suite manufactures itself.

Measured on the machine where this was written, 31 reports split three ways:

    27  EXC_BREAKPOINT on Chrome_ChildIOThread   children aborting when
                                                 test_stability_chaos_live
                                                 kills the shared driver
     3  EXC_BAD_ACCESS on CrRendererMain         its CDP `Page.crash`
     1  EXC_BREAKPOINT on CrBrowserMain          the real, unexplained crash

So the signal is buried 30:1, and the default invocation hands you noise. A
correlation run against a real chaos crash put three
`test_stability_chaos_live` entries in the window with nothing to distinguish
them from an ordinary test that happened to be running.

The note is derived by scanning the correlated module for the mechanisms rather
than listing test names, so a chaos test added later is covered without editing
the script -- the same reason `macros/runtime` derives RECORDER_NOISE instead
of mirroring it by hand. A hand-kept list is exactly what drifts, and drift
here means a manufactured crash reads as a defect.

Matching is by substring, which cannot tell "uses the mechanism" from "mentions
it" -- a limitation this very file demonstrated by flagging itself the moment it
named the markers in a parametrize. The markers are therefore read from the
script rather than spelled here, and the false positive is asserted below as a
known property rather than left to surprise the next reader.

Selection is fixed separately and more deeply: `newest_crash_report(thread=...)`
lets the caller ask for the crash CLASS they are hunting, which is what the 30:1
burial actually costs them. That is deliberately not a "skip the manufactured
ones" filter -- the chaos tests crash a real renderer, so their reports are
signature-identical to a genuine renderer crash, and only correlation can tell
the two apart.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from tests._script_module import load_script_module

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "watch_test_timeline.py"


def _load() -> ModuleType:
    return load_script_module("scripts/watch_test_timeline.py")


@pytest.fixture(scope="module")
def timeline() -> ModuleType:
    return _load()


class TestDeliberateCrashDetection:
    def test_the_real_chaos_module_is_recognized(self, timeline: ModuleType) -> None:
        """Not a synthetic fixture: the module that actually manufactures the noise."""
        assert timeline.induces_deliberate_crashes("tests/test_stability_chaos_live.py")

    def test_an_ordinary_test_module_is_not(self, timeline: ModuleType) -> None:
        assert not timeline.induces_deliberate_crashes("tests/test_engines.py")

    @pytest.mark.parametrize(
        ("label", "template"),
        [
            ("called", "async def test_x():\n    await thing.{marker}\n"),
            ("mentioned in prose", '"""Prose that only names {marker} and calls nothing."""\n'),
        ],
    )
    @pytest.mark.parametrize("index", [0, 1])
    def test_a_module_carrying_a_mechanism_is_flagged(
        self,
        timeline: ModuleType,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        label: str,
        template: str,
        index: int,
    ) -> None:
        """Either mechanism is enough, and prose counts -- the accepted false positive.

        Substring matching cannot separate a module that CALLS the mechanism
        from one that merely names it; this file tripped it on itself. Pinned as
        a decision rather than left to surprise the next reader: the note means
        "check whether this was deliberate", never proof that it was. Markers are
        read from the script, since spelling one here would match the detector.
        """
        module = tmp_path / "tests" / "test_synthetic.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(template.format(marker=timeline._DELIBERATE_CRASH_MARKERS[index]), encoding="utf-8")
        monkeypatch.setattr(timeline, "ROOT", tmp_path)
        assert timeline.induces_deliberate_crashes("tests/test_synthetic.py"), label


class TestAnnotation:
    def test_a_chaos_row_is_annotated(self, timeline: ModuleType) -> None:
        row = "call tests/test_stability_chaos_live.py::test_dead_driver_self_heals_on_next_launch"
        annotated = timeline.annotate(row)
        assert annotated.startswith(row), "the original row must survive verbatim"
        assert timeline._DELIBERATE_NOTE in annotated

    def test_an_ordinary_row_is_returned_unchanged(self, timeline: ModuleType) -> None:
        row = "setup tests/test_engines.py::test_engine_status_parses_current_version"
        assert timeline.annotate(row) == row

    def test_the_phase_prefix_is_not_mistaken_for_the_module(self, timeline: ModuleType) -> None:
        """A row is `<phase> <path>::<test>`; splitting on "::" alone would keep the phase."""
        assert timeline.annotate("teardown tests/test_stability_chaos_live.py::test_x").endswith(
            timeline._DELIBERATE_NOTE
        )

    def test_a_row_with_no_phase_prefix_still_resolves(self, timeline: ModuleType) -> None:
        """`partition` yields an empty tail when there is no space; fall back to the whole row."""
        assert timeline.annotate("tests/test_stability_chaos_live.py::test_x").endswith(timeline._DELIBERATE_NOTE)

    def test_a_module_path_containing_a_space_still_resolves(
        self, timeline: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REGRESSION: taking what followed the LAST space assumed paths have none.

        `rpartition(" ")` turned "call tests/my test.py::x" into "test.py", the
        read failed, `induces_deliberate_crashes` swallowed the OSError, and the
        row came back unannotated with nothing to say it had misparsed. Stripping
        a known phase word instead is a branch, but a correct one.
        """
        module = tmp_path / "tests" / "my test.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(f"x = {timeline._DELIBERATE_CRASH_MARKERS[0]!r}\n", encoding="utf-8")
        monkeypatch.setattr(timeline, "ROOT", tmp_path)

        assert timeline.annotate("call tests/my test.py::test_x").endswith(timeline._DELIBERATE_NOTE)

    def test_a_phase_like_word_that_is_not_a_phase_is_not_stripped(self, timeline: ModuleType) -> None:
        """Only the phases conftest writes are stripped, not any first word."""
        assert timeline.annotate("tests/test_stability_chaos_live.py::test_x").endswith(timeline._DELIBERATE_NOTE)
