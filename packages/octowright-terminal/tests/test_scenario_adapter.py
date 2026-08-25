# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The adapter that replaces core's hardcoded terminal branch.

Terminal is the case the capability vocabulary was designed around: a kind that
can JOIN a scenario but cannot run macros or sync. The negative assertions below
are the point of the file -- they pin that the narrowing actually narrows.
"""

from __future__ import annotations

from octowright_terminal.scenario import TerminalScenarioAdapter

from octowright.plugins.contract import (
    ScenarioAdapter,
    SupportsDialogPolicy,
    SupportsMacros,
    SupportsMockRoutes,
    SupportsSync,
)


def test_the_adapter_satisfies_the_mandatory_floor():
    adapter = TerminalScenarioAdapter(pool=object())
    assert isinstance(adapter, ScenarioAdapter)


def test_the_adapter_claims_no_capability_it_cannot_honour():
    adapter = TerminalScenarioAdapter(pool=object())
    assert not isinstance(adapter, SupportsMacros)
    assert not isinstance(adapter, SupportsSync)
    assert not isinstance(adapter, SupportsDialogPolicy)
    assert not isinstance(adapter, SupportsMockRoutes)


def test_resolve_participant_returns_pty_launch_kwargs():
    from octowright.scenarios import Participant

    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(persona="ops", kind="terminal", role="operator", options={"connector_type": "pty"})
    kwargs = adapter.resolve_participant(p, persona=None)
    assert kwargs["kind"] == "pty", "connector type, not the session kind"
    assert kwargs["profile"] == "ops"


def test_an_unsupported_connector_type_is_refused():
    import pytest

    from octowright.scenarios import Participant

    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(persona="ops", kind="terminal", role="operator", options={"connector_type": "carrier-pigeon"})
    with pytest.raises(ValueError, match="connector_type"):
        adapter.resolve_participant(p, persona=None)
