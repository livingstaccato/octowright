# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The gate the deletion phase is allowed to proceed past.

Spec §12: nothing leaves core (``src/octowright/terminal/``,
``src/octowright/server/terminal/``, the seven terminal tools, the terminal
branch of the dashboard/scenario layers) until this package passes the same
contract the in-repo reference plugin (``tests/plugins/reference/``) passes.
That reference suite is split across two kinds of test, and this module only
reproduces the first kind for the terminal package:

* **Contract tests** — assert something true of *any* conforming plugin, with
  no dependency on ``tests.plugins.reference`` specifics:
  ``tests/plugins/test_contract.py`` in full (the ``_CAPABILITY_PROTOCOLS``
  vocabulary, its closure, ``PLUGIN_API_VERSION``'s tie to the Protocol
  shapes) and, from ``tests/plugins/test_reference_plugin.py``, only
  ``test_reference_pool_covers_every_session_pool_method`` — the "anti-decay
  guard" the docstring names explicitly, which fails when a method is added
  to ``SessionPool`` without the reference pool covering it. Both are
  guards against CORE's contract decaying, so they run once (against the
  reference plugin, in ``tests/plugins/``) and are not re-run per plugin;
  this module instead re-derives their assertions against
  ``octowright_terminal.plugin.plugin`` so the same drift is caught on the
  terminal side of the seam.
* **Reference-specific behaviour** — everything else in ``tests/plugins/``:
  ``test_reference_plugin.py``'s launch/close/protection tests,
  ``test_reference_scenario.py``, ``test_reference_frontend.py``,
  ``test_reference_activation.py``, and ``test_reference_artifacts.py`` all
  exercise the toy ``refkind`` plugin's OWN behaviour (its recorded rows, its
  fake artifact, its dashboard route wiring) to prove the *loader and HTTP
  layer* work end to end. They are not reusable as-is against another
  plugin — terminal already has its own equivalents
  (``test_pool_contract.py``, ``test_scenario_adapter.py``,
  ``test_frontend_asset.py``, ``test_entry_point.py``,
  ``test_session_detail.py``) exercising the same seams with terminal's own
  fixtures (a real PTY, not a fake recorder). This module does not duplicate
  those; it asserts the shape-level guarantees a hand-written per-seam test
  can silently stop covering when core's contract grows.

Every assertion below is driven off ``octowright.plugins.contract`` itself —
``SessionPool``'s declared members, and ``_CAPABILITY_PROTOCOLS``'s table —
rather than a hand-copied list, so a capability or pool method added to core
without a corresponding decision recorded here fails this test instead of
silently passing.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest
from octowright_terminal.plugin import plugin

from octowright.http.routes._session_kinds import plugin_session_detail
from octowright.plugins.contract import _CAPABILITY_PROTOCOLS, FrontendAsset, SessionPool
from octowright.server import plugin_state
from octowright.server._state import mcp


@pytest.fixture
def pool() -> Any:
    # The `_activated_terminal_plugin` autouse fixture in conftest.py already
    # built and registered a real TerminalPool for this test via a fresh
    # PluginRegistry -- reused here rather than constructing a second one, so
    # this module's pool is the SAME object `plugin_session_detail` resolves
    # through `plugin_state.registry()` (see the session_detail test below).
    return plugin_state.registry().pools()["terminal"]


def _public_protocol_methods(proto: type) -> dict[str, Any]:
    """Every non-dunder callable a Protocol declares in its own body."""
    return {name: value for name, value in vars(proto).items() if not name.startswith("_") and callable(value)}


def _assert_signature_compatible(name: str, proto_func: Any, bound_impl: Any) -> None:
    """The pool method must accept every call the ``SessionPool`` contract makes.

    ``inspect.Signature.bind`` only checks arity/keyword-ness, never types, so
    the sample values below are placeholders -- what matters is each
    parameter's *kind* (positional-or-keyword vs. keyword-only), taken from
    the protocol's own signature, not hand-copied per method.

    ``launch`` is exempted from binding: its contract signature is
    ``**kwargs: Any`` because core makes no promise about argument names
    there -- a plugin's own callers (its tool module, its scenario adapter)
    know the kind-specific kwargs its pool needs (``connector_config`` for
    terminal). Only the async/sync calling convention is checked for it.
    """
    proto_params = list(inspect.signature(proto_func).parameters.values())[1:]  # drop `self`
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in proto_params):
        return
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for param in proto_params:
        if param.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[param.name] = None
        else:
            args.append(None)
    try:
        inspect.signature(bound_impl).bind(*args, **kwargs)
    except TypeError as exc:
        pytest.fail(f"TerminalPool.{name} is not call-compatible with the SessionPool contract: {exc}")


def test_pool_covers_every_session_pool_method_with_a_compatible_signature(pool: Any) -> None:
    # Re-derivation of tests/plugins/test_reference_plugin.py's
    # `test_reference_pool_covers_every_session_pool_method` anti-decay guard,
    # against the terminal pool instead of the reference one, plus a
    # signature check that name-only coverage cannot see.
    required = _public_protocol_methods(SessionPool)
    assert required, "SessionPool declared no public methods; the contract itself looks broken"
    for name, proto_func in required.items():
        impl = getattr(pool, name, None)
        assert callable(impl), f"TerminalPool is missing SessionPool.{name}"
        assert inspect.iscoroutinefunction(proto_func) == inspect.iscoroutinefunction(impl), (
            f"TerminalPool.{name} disagrees with SessionPool.{name} on sync vs. async"
        )
        _assert_signature_compatible(name, proto_func, impl)

    # Every non-async method must also be reachable without a running loop --
    # the same closing assertion the reference guard makes.
    assert pool.maybe_get("nope") is None
    with pytest.raises(KeyError):
        pool.get("nope")


#: Terminal's deliberate coverage of core's capability vocabulary. Named
#: explicitly -- not computed as "whatever isn't implemented" -- so a
#: capability added to (or removed from) `_CAPABILITY_PROTOCOLS` without a
#: decision recorded here fails the assertion below rather than silently
#: landing in "absent". Terminal implements ONLY the mandatory floor
#: (`resolve_participant`); see `octowright_terminal.scenario`'s module
#: docstring for why -- it cannot run macros or sync in any scenario today.
EXPECTED_ABSENT_CAPABILITIES = frozenset({"macros", "sync", "dialog_policy", "mock_routes"})


def test_every_capability_is_implemented_or_deliberately_absent(pool: Any) -> None:
    # Vocabulary-closure guard: this test's own expectations must cover every
    # capability core's loader currently derives support from -- the live
    # table, not a hand-copied snapshot of it.
    assert set(_CAPABILITY_PROTOCOLS) == EXPECTED_ABSENT_CAPABILITIES, (
        "core's capability vocabulary changed shape (see _CAPABILITY_PROTOCOLS in "
        "octowright.plugins.contract) without EXPECTED_ABSENT_CAPABILITIES above being "
        "updated to make a deliberate call on whether terminal implements the new one"
    )

    adapter = plugin.create_scenario_adapter(pool)
    assert adapter is not None, "terminal declares scenario support (SCENARIO_KINDS), so it must build an adapter"

    for name, proto in _CAPABILITY_PROTOCOLS.items():
        # Every member is EITHER implemented (isinstance holds) OR its
        # absence is asserted explicitly -- for terminal, all four are
        # deliberately absent, and that is the point of this test, not
        # filler: a regression that silently starts satisfying one of these
        # Protocols (e.g. a `run_macro` added to the adapter for an
        # unrelated reason) is exactly what this line is watching for.
        assert not isinstance(adapter, proto), (
            f"terminal's scenario adapter now satisfies {proto.__name__} ({name!r}) -- "
            "update EXPECTED_ABSENT_CAPABILITIES if this is deliberate"
        )


def test_tool_names_are_nonempty_collision_free_and_match_registration() -> None:
    assert plugin.tool_names, "terminal plugin declares no tools"
    assert plugin.tool_module, "terminal plugin declares no tool module"

    # Names owned by anything OTHER than this plugin's own tool module --
    # core's ~129 tools, plus any other plugin active in this process. None of
    # terminal's declared names may already be among them; this is the same
    # collision core's loader itself refuses at activation (core registers
    # every submodule before any plugin activates -- see
    # `_plugin_activation`'s module docstring -- so `add_tool` is first-wins).
    other_names = {name for name, tool in mcp._tool_manager._tools.items() if tool.fn.__module__ != plugin.tool_module}
    assert plugin.tool_names.isdisjoint(other_names), (
        f"terminal tool name collides with an existing tool: {sorted(plugin.tool_names & other_names)}"
    )

    # Importing tool_module is idempotent (a no-op if some earlier test in
    # this session already imported it -- e.g. test_mcp_tools.py), and
    # `add_tool` on an already-registered name just warns and returns the
    # existing Tool (see mcp.server.mcpserver.tools.tool_manager.ToolManager.
    # add_tool), so re-importing here is safe regardless of collection order.
    importlib.import_module(plugin.tool_module)
    registered_by_module = {
        name for name, tool in mcp._tool_manager._tools.items() if tool.fn.__module__ == plugin.tool_module
    }
    assert registered_by_module == plugin.tool_names, (
        "plugin.tool_names has drifted from what octowright_terminal.tools actually registers: "
        f"declared-only={sorted(plugin.tool_names - registered_by_module)}, "
        f"registered-only={sorted(registered_by_module - plugin.tool_names)}"
    )


async def test_session_detail_returns_a_mapping_carrying_id_and_kind(pool: Any) -> None:
    # The raw `plugin.session_detail(session)` call contributes only
    # terminal's own fields (connector_type, action_count, the browser-only
    # Nones) -- `id`/`kind` come from core's uniform `_live_summary` base that
    # `plugin_session_detail` merges them under (see
    # `octowright.http.routes._session_kinds.plugin_session_detail` and
    # `test_session_detail.py`'s own merged-payload test). The contract's
    # promise is about what a caller actually receives through that seam, so
    # this drives the real merge rather than the plugin's raw contribution.
    launched = await pool.launch(kind="pty", connector_config={"command": "/bin/cat"})
    instance_id = launched["instance_id"]
    try:
        session = pool.get(instance_id)
        detail = plugin_session_detail("terminal", session)
        assert isinstance(detail, dict)
        assert detail.get("id") == instance_id
        assert detail.get("kind") == "terminal"
    finally:
        await pool.close(instance_id, force=True)


def test_frontend_is_a_frontend_asset_with_a_real_module_on_disk() -> None:
    frontend = plugin.frontend
    assert isinstance(frontend, FrontendAsset)
    assert frontend.asset_dir.is_dir()
    assert (frontend.asset_dir / frontend.module_path).is_file()
