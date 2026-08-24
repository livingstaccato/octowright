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
