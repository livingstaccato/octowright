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

from types import SimpleNamespace

import pytest
from octowright_terminal.scenario import TerminalScenarioAdapter

from octowright.plugins.contract import (
    ScenarioAdapter,
    SupportsDialogPolicy,
    SupportsMacros,
    SupportsMockRoutes,
    SupportsSync,
)
from octowright.scenarios import Participant


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
    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(
        persona="ops", kind="terminal", role="operator", options={"connector_type": "pty", "command": "/bin/sh"}
    )
    kwargs = adapter.resolve_participant(p, persona=None)
    assert kwargs["kind"] == "pty", "connector type, not the session kind"
    assert kwargs["connector_config"] == {"command": "/bin/sh", "cols": 80, "rows": 24}
    assert kwargs["profile"] == "ops" and kwargs["protected"] is False and kwargs["label"] is None


def test_pty_is_the_default_connector():
    """``connector_type`` omitted from ``options`` still resolves to pty."""
    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(persona="ops", kind="terminal", role="operator")
    kwargs = adapter.resolve_participant(p, persona=None)
    assert kwargs["kind"] == "pty"
    assert kwargs["connector_config"]["command"] == "/bin/bash"


def test_an_unsupported_connector_type_is_refused():
    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(persona="ops", kind="terminal", role="operator", options={"connector_type": "carrier-pigeon"})
    with pytest.raises(ValueError, match="connector_type"):
        adapter.resolve_participant(p, persona=None)


def test_ssh_explicit_args_win_over_persona_defaults():
    """participant override -> persona app.ssh default -> omit, moved verbatim
    from core's TestResolveTerminalLaunch (deleted in step 5's core cleanup)."""
    ssh_defaults = {"host": "default-host", "user": "deploy", "key_path": "/k", "known_hosts": "/kh"}
    persona = SimpleNamespace(app={"ssh": ssh_defaults})
    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(
        persona="a",
        kind="terminal",
        role="op",
        options={"connector_type": "ssh", "host": "explicit-host"},
    )
    kwargs = adapter.resolve_participant(p, persona=persona)
    cfg = kwargs["connector_config"]
    assert kwargs["kind"] == "ssh"
    assert cfg["host"] == "explicit-host"  # participant wins
    assert cfg["username"] == "deploy"  # persona default
    assert cfg["client_key_path"] == "/k" and cfg["known_hosts"] == "/kh"
    assert "command" not in cfg and "cols" not in cfg


def test_ssh_without_persona_or_args_omits_optionals():
    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(persona="ghost", kind="terminal", role="op", options={"connector_type": "ssh"})
    kwargs = adapter.resolve_participant(p, persona=None)
    cfg = kwargs["connector_config"]
    # No host/user/key/known_hosts anywhere -> only the default port survives.
    assert cfg == {"port": 22}


@pytest.mark.parametrize("opt", ["cols", "rows", "port"])
def test_a_non_integer_option_is_refused(opt):
    """``options`` is opaque to core, so the YAML parser's int check does not
    cover these; the adapter must catch a string ``cols`` before it reaches
    the uterm connector."""
    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(persona="t", kind="terminal", role="monitor", options={opt: "80"})
    with pytest.raises(ValueError, match=f"options.{opt} must be an integer"):
        adapter.resolve_participant(p, persona=None)


@pytest.mark.parametrize("opt", ["cols", "rows", "port"])
def test_a_bool_option_is_refused(opt):
    """``bool`` is an ``int`` subclass, so a bare isinstance check would let
    ``cols: true`` through."""
    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(persona="t", kind="terminal", role="monitor", options={opt: True})
    with pytest.raises(ValueError, match=f"options.{opt} must be an integer"):
        adapter.resolve_participant(p, persona=None)


def test_integer_options_are_accepted():
    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(persona="t", kind="terminal", role="monitor", options={"cols": 100, "rows": 40, "port": 2222})
    adapter.resolve_participant(p, persona=None)  # must not raise


def test_the_shipped_example_scenario_parses_and_resolves_through_this_adapter():
    """`examples/scenarios/browser-plus-terminal.yaml` is the only worked
    example of the `options:` shape, and the extraction left it validated by
    nothing (`tests/test_scenarios_terminal.py` was deleted). It belongs on
    this side of the seam now: the file is core's, but the `options:` block it
    documents is terminal's, and only this adapter can say the documented keys
    still mean what the comment claims.
    """
    from pathlib import Path

    from octowright_terminal.plugin import plugin as terminal_plugin

    from octowright.plugins.registry import PluginRegistry
    from octowright.scenarios import load_yaml_scenario
    from octowright.server import plugin_state

    # Three levels up from tests/: packages/octowright-terminal/ -> packages/ -> repo root.
    path = Path(__file__).resolve().parents[3] / "examples" / "scenarios" / "browser-plus-terminal.yaml"
    assert path.is_file(), f"the example scenario moved or was deleted: {path}"

    # `known_kinds()` lists a plugin kind only once it has an ADAPTER, and the
    # suite's autouse fixture registers terminal with `adapter=None` (its tests
    # do not need one). Register a real adapter so `kind: terminal` validates
    # the way it does in a daemon with the plugin enabled -- which is also the
    # half of `load_yaml_scenario` this example is meant to prove.
    registry = PluginRegistry()
    pool = plugin_state.registry().pools()["terminal"]
    registry.register(
        terminal_plugin, pool=pool, adapter=terminal_plugin.create_scenario_adapter(pool), discovered=None
    )
    previous = plugin_state.registry()
    plugin_state.set_registry(registry)
    try:
        spec = load_yaml_scenario(path.read_text(encoding="utf-8"), "browser-plus-terminal")
    finally:
        plugin_state.set_registry(previous)
    by_kind = {p.kind: p for p in spec.participants}
    assert set(by_kind) == {"chromium", "terminal"}

    term = by_kind["terminal"]
    assert term.role == "operator"
    assert term.options["connector_type"] == "pty"
    assert term.options["command"] == "/bin/bash"

    # And the adapter turns those documented keys into a real launch kwargs map.
    resolved = TerminalScenarioAdapter(pool=object()).resolve_participant(term, None)
    assert resolved["kind"] == "pty"
    assert resolved["connector_config"]["command"] == "/bin/bash"
