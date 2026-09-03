# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Root conftest — copied by mutmut to mutants/conftest.py.

Lets `make mutmut` run on macOS Python 3.11+ without crashing every mutant.
Two bugs in mutmut 3.5.0 + macOS bite this project:

1. ``multiprocessing.set_start_method('fork')`` is called both by mutmut at
   import time and by the trampoline injected into mutated modules. The
   second call raises ``RuntimeError: context has already been set``, which
   bubbles up as "segfault" in the mutmut tally. We patch
   ``multiprocessing.set_start_method`` to swallow that specific error.

2. ``setproctitle()`` segfaults in a forked child on macOS. mutmut calls it
   from each child process to label the mutant under test. We register an
   at-fork handler that no-ops mutmut's ``setproctitle`` binding in the
   child before any user code runs.

Both patches only apply when running under mutmut (i.e. CWD is the
``mutants/`` workdir). Normal pytest runs are unaffected.

Pattern lifted from provide-uterm's conftest. See
https://github.com/sjkelly/mutmut/issues for the upstream tracking issues.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent
_mutants_src = _here / "src"
_running_under_mutmut = _here.name == "mutants" and _mutants_src.exists()

if _running_under_mutmut:
    import multiprocessing as _mp
    import os as _os

    _orig_set_start = _mp.set_start_method

    def _safe_set_start(method: str, force: bool = False) -> None:
        import contextlib

        with contextlib.suppress(RuntimeError):
            _orig_set_start(method, force=force)

    _mp.set_start_method = _safe_set_start  # type: ignore[attr-defined]

    def _noop_setproctitle_in_child() -> None:
        try:
            import mutmut.__main__ as _mm

            _mm.setproctitle = lambda _t: None  # type: ignore[attr-defined]
        except Exception:
            pass

    _os.register_at_fork(after_in_child=_noop_setproctitle_in_child)


def pytest_addoption(parser: pytest.Parser, pluginmanager: pytest.PytestPluginManager) -> None:
    """Keep ``--randomly-seed`` parseable when pytest-randomly is unloaded.

    ``addopts`` pins ``--randomly-seed`` so a bare ``pytest`` is reproducible,
    and that option is registered by pytest-randomly. Unloading the plugin with
    ``-p no:randomly`` therefore unregisters the option that ``addopts`` still
    passes, and pytest exits 4 with ``unrecognized arguments:
    --randomly-seed=...`` before collecting anything.

    That is a documented trap for anyone typing the flag by hand, but mutmut
    3.x hardcodes ``["-x", "-q", "-p", "no:randomly", "-p", "no:random-order"]``
    (``mutmut/__main__.py``) with no way to configure it off, so ``make mutmut``
    could not run at all -- the nightly job failed for three consecutive nights
    with ``BadTestExecutionCommandsException`` while still reporting
    ``survived: 0``, which reads like a passing mutation score rather than a
    harness that never started. pytest-randomly registers no ini option, so the
    seed cannot move out of ``addopts`` to sidestep this.

    Registering an inert stand-in only when the real plugin is absent keeps the
    flag parseable without shadowing it when it is present. Seeding is
    pytest-randomly's job; with the plugin unloaded there is no ordering to
    seed, so accepting and ignoring the value is the whole contract.
    """
    if pluginmanager.hasplugin("randomly"):
        return
    parser.addoption(
        "--randomly-seed",
        action="store",
        default=None,
        help="Inert stand-in accepted while pytest-randomly is unloaded (see conftest).",
    )


@pytest.fixture(autouse=True)
def _isolate_session_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests from writing stale browser entries to the user's manifest.

    Individual tests that assert manifest behavior can still monkeypatch this
    path again after the autouse fixture runs.
    """
    from octowright import defaults as _defaults
    from octowright import session_manifest as _manifest

    manifest_path = tmp_path / "session-manifest.json"
    monkeypatch.setattr(_defaults, "SESSION_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(_manifest, "SESSION_MANIFEST_PATH", manifest_path)


@pytest.fixture(autouse=True)
def _isolate_advisor_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests from reading/writing the user's Advisor state JSON.

    Mirrors ``_isolate_session_manifest``: every test gets a per-tmp_path
    advisor.json so preferences, tool usage, and macro observations stay
    isolated. Individual tests can still monkeypatch the symbol again.
    """
    from octowright import advisor as _advisor

    advisor_state = tmp_path / "advisor.json"
    monkeypatch.setattr(_advisor, "ADVISOR_STATE_PATH", advisor_state)
