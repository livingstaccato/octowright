# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Convention test: connector enumeration order + external MCP arg preservation.

Internal connectors must enumerate network (ssh, telnet) before local (pty).
The external ``terminal_launch`` MCP tool arg ``kind`` is an external contract
and must remain unchanged regardless of internal reordering.
"""

from __future__ import annotations

import inspect

from octowright.terminal import connector_config

# Canonical ordering: network modern→legacy (ssh, telnet), then local (pty).
# octowright has NO ws connector; do not add one (YAGNI).
_NETWORK_CANON = ["ssh", "telnet"]
_LOCAL = {"pty"}


def _builder_kinds_from_all() -> list[str]:
    """Derive kind tokens from ``__all__`` entries like ``ssh_connector_config`` → ``ssh``."""
    return [
        name.replace("_connector_config", "") for name in connector_config.__all__ if name.endswith("_connector_config")
    ]


def test_connector_enumeration_canonical_order() -> None:
    """Network connectors (ssh, telnet) enumerate before local (pty) in __all__."""
    kinds = _builder_kinds_from_all()
    network = [k for k in kinds if k not in _LOCAL]
    local = [k for k in kinds if k in _LOCAL]
    assert network == _NETWORK_CANON, f"network connectors out of order: {network}"
    assert kinds == network + local, f"local must trail network: {kinds}"


def test_external_mcp_terminal_launch_kind_arg_preserved() -> None:
    """The external MCP tool arg name ``kind`` and its default ``'pty'`` are an external
    contract for MCP clients — assert they are UNCHANGED by any internal reordering."""
    from octowright.server.terminal.lifecycle import terminal_launch

    sig = inspect.signature(terminal_launch)
    assert "kind" in sig.parameters, "external MCP arg 'kind' was renamed — breaks MCP clients"
    param = sig.parameters["kind"]
    assert param.default == "pty", f"external default for 'kind' changed: {param.default!r}"
    # annotation is a string under PEP 563 (from __future__ import annotations)
    assert param.annotation in (str, "str"), f"external type for 'kind' changed: {param.annotation!r}"
    # Implemented dispatch values (ssh / telnet / else→pty) must all still be present
    # in the function body — guard against accidental deletion of a branch.
    src = inspect.getsource(terminal_launch)
    assert '"ssh"' in src, "ssh dispatch branch missing from terminal_launch"
    assert '"telnet"' in src, "telnet dispatch branch missing from terminal_launch"
