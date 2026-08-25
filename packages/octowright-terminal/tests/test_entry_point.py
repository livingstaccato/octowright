# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The entry point is the whole interface between core and this package.

Core finds the plugin by name in the ``octowright.session_kinds`` group and
never imports ``octowright_terminal`` directly. If this test fails, the package
is invisible no matter how correct everything inside it is.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from octowright.plugins.contract import PLUGIN_API_VERSION


def test_the_entry_point_is_discoverable_by_name():
    eps = [e for e in entry_points(group="octowright.session_kinds") if e.name == "terminal"]
    assert len(eps) == 1, "expected exactly one 'terminal' entry point in octowright.session_kinds"


def test_the_entry_point_resolves_to_a_descriptor_core_accepts():
    (ep,) = [e for e in entry_points(group="octowright.session_kinds") if e.name == "terminal"]
    descriptor = ep.load()
    assert descriptor.kind == "terminal"
    assert descriptor.plugin_api_version == PLUGIN_API_VERSION
    assert descriptor.display_name


def test_the_declared_api_version_is_a_literal_not_cores_constant():
    """The gate only gates if the two can disagree.

    ``plugin_api_version = PLUGIN_API_VERSION`` agrees by construction, so a
    core bump is auto-adopted and the loader's refusal path -- the legible
    "does not match core's N" message -- is unreachable for this plugin. This
    package is an independently released distribution installable beside a core
    it was never built with, so it states its own number.

    The assertion above (``== PLUGIN_API_VERSION``) is what makes a core bump
    fail HERE, forcing a deliberate decision, instead of shipping a plugin core
    silently refuses at runtime.
    """
    import ast
    import inspect

    from octowright_terminal import plugin as plugin_module

    tree = ast.parse(inspect.getsource(plugin_module))
    (cls,) = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "TerminalPlugin"]
    assigned = [
        n.value
        for n in cls.body
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "plugin_api_version" for t in n.targets)
    ]
    assert len(assigned) == 1, "TerminalPlugin must declare plugin_api_version exactly once"
    assert isinstance(assigned[0], ast.Constant), (
        "plugin_api_version must be a literal; importing core's constant makes the version gate a no-op"
    )
