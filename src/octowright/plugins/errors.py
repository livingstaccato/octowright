# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exception types shared across the plugin surface.

These live in their own module rather than beside their raisers because both
core and third-party plugin code import them, and a plugin importing
``octowright.plugins.loader`` to reach an exception would pull the whole load
machinery into its import graph.
"""

from __future__ import annotations


class PluginError(Exception):
    """Base for every plugin-surface error."""


class PluginLoadError(PluginError):
    """A plugin failed to resolve, validate, or activate."""


class DuplicatePluginNameError(PluginLoadError):
    """Two installed distributions declare the same entry-point name.

    Refused outright rather than resolved by enumeration order, which is
    installation-dependent and would make behaviour vary by machine.
    """


class ProtectedSessionCloseError(PluginError):
    """A close was refused because the session is protected and ``force`` was not set."""


class SessionIdInUseError(PluginLoadError):
    """A launch committed an ``instance_id`` another registered pool already holds.

    Core resolves a session by id alone across every pool, so ids must be
    globally unique, not merely unique within one pool.
    """


class ControlBudgetExceededError(PluginError):
    """A control row would exceed the recorder's separate control-row budget.

    Raised rather than silently dropped: the whole point of a control row is
    that its absence is never invisible.
    """
