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
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "watch_test_timeline.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("watch_test_timeline", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def timeline() -> ModuleType:
    return _load()


class TestDeliberateCrashDetection:
    def test_the_real_chaos_module_is_recognized(self, timeline: ModuleType) -> None:
        """Not a synthetic fixture: the module that actually manufactures the noise."""
        assert timeline.induces_deliberate_crashes("tests/test_stability_chaos_live.py")

    def test_an_ordinary_test_module_is_not(self, timeline: ModuleType) -> None:
        assert not timeline.induces_deliberate_crashes("tests/test_engines.py")

    def test_a_module_that_only_mentions_a_mechanism_is_flagged(
        self, timeline: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The accepted false positive, pinned so it is a decision and not a surprise.

        Substring matching cannot separate a module that CALLS the mechanism from
        one that merely names it in prose. This file tripped it on itself. The
        note is a hint to check, never proof that a crash was deliberate.
        """
        module = tmp_path / "tests" / "test_docs_only.py"
        module.parent.mkdir(parents=True)
        marker = timeline._DELIBERATE_CRASH_MARKERS[0]
        module.write_text(f'"""Prose that only names {marker} and calls nothing."""\n', encoding="utf-8")
        monkeypatch.setattr(timeline, "ROOT", tmp_path)
        timeline.induces_deliberate_crashes.cache_clear()
        assert timeline.induces_deliberate_crashes("tests/test_docs_only.py")

    def test_an_unreadable_path_is_not_deliberate(self, timeline: ModuleType) -> None:
        """Guessing the other way would dismiss a real crash as expected."""
        assert not timeline.induces_deliberate_crashes("tests/does_not_exist_anywhere.py")

    @pytest.mark.parametrize("index", [0, 1])
    def test_each_marker_is_sufficient_on_its_own(
        self, timeline: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, index: int
    ) -> None:
        """Either mechanism crashes a browser, so either alone must be enough.

        The marker is read from the script rather than written here: spelling it
        would make this module match its own detector.
        """
        marker = timeline._DELIBERATE_CRASH_MARKERS[index]
        module = tmp_path / "tests" / "test_synthetic.py"
        module.parent.mkdir(parents=True)
        module.write_text(f"async def test_x():\n    await thing.{marker}\n", encoding="utf-8")
        monkeypatch.setattr(timeline, "ROOT", tmp_path)
        timeline.induces_deliberate_crashes.cache_clear()
        assert timeline.induces_deliberate_crashes("tests/test_synthetic.py")


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
