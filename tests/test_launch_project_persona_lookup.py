# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""An unlabelled launch looks for a persona named after the project; a broken one is logged.

``browser_launch`` and ``browser_quick_launch`` both tried ``load_persona(slug)``
under ``except Exception: pass``. A persona that simply does not exist is the
expected case, but a persona file that exists and cannot be loaded (invalid
YAML, a field that fails validation, unreadable) was swallowed identically: the
browser launched on a throwaway profile with no trace of why the operator's
persona -- and its saved login -- was not used.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright import personas
from octowright.server.browser import lifecycle


class _Log:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **fields: Any) -> None:
        self.warnings.append((event, fields))

    def debug(self, event: str, **fields: Any) -> None:
        del event, fields


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> _Log:
    fake = _Log()
    monkeypatch.setattr(lifecycle, "log", fake)
    return fake


def test_an_existing_persona_named_after_the_project_is_used(monkeypatch: pytest.MonkeyPatch, log: _Log) -> None:
    monkeypatch.setattr(personas, "load_persona", lambda name: object())

    assert lifecycle._project_slug_persona("tim/octowright") == "octowright"
    assert log.warnings == []


def test_a_missing_persona_is_the_quiet_expected_case(monkeypatch: pytest.MonkeyPatch, log: _Log) -> None:
    def missing(name: str) -> object:
        raise FileNotFoundError(name)

    monkeypatch.setattr(personas, "load_persona", missing)

    assert lifecycle._project_slug_persona("octowright") is None
    assert log.warnings == []


def test_a_persona_that_exists_but_cannot_load_is_logged(monkeypatch: pytest.MonkeyPatch, log: _Log) -> None:
    def broken(name: str) -> object:
        raise ValueError(f"invalid persona file {name}: credentials must be a mapping")

    monkeypatch.setattr(personas, "load_persona", broken)

    assert lifecycle._project_slug_persona("tim/octowright") is None
    [(event, fields)] = log.warnings
    assert event == "octowright.launch.project_persona_unloadable"
    assert fields["persona"] == "octowright"
    assert "credentials must be a mapping" in fields["error"]
