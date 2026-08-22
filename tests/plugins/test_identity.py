# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.plugins.errors import PluginLoadError
from octowright.plugins.identity import validate_kind, validate_name, validate_tool_names


@pytest.mark.parametrize("value", ["terminal", "a", "my-kind", "my_kind2", "a" * 64])
def test_valid_names(value):
    validate_name(value, label="entry-point name")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Terminal",
        "2fast",
        "-lead",
        "has space",
        "has/slash",
        "a" * 65,
        "with.dot",
        "terminal\n",
        "terminal\r",
        "terminal ",
    ],
)
def test_invalid_names(value):
    with pytest.raises(PluginLoadError, match="entry-point name"):
        validate_name(value, label="entry-point name")


@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit", "browser", "unknown", "session"])
def test_reserved_kinds_are_refused(kind):
    with pytest.raises(PluginLoadError, match="reserved"):
        validate_kind(kind)


def test_non_reserved_kind_passes():
    validate_kind("refkind")


def test_tool_names_must_carry_the_kind_prefix():
    validate_tool_names("refkind", frozenset({"refkind_launch", "refkind_close"}))
    with pytest.raises(PluginLoadError, match="must start with 'refkind_'"):
        validate_tool_names("refkind", frozenset({"browser_launch"}))


def test_empty_tool_names_is_allowed():
    validate_tool_names("refkind", frozenset())
