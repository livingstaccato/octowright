# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.plugins.errors import PluginLoadError
from octowright.plugins.identity import (
    validate_instance_id,
    validate_kind,
    validate_name,
    validate_tool_names,
)


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


def test_kind_forbids_the_hyphen_that_would_shift_the_filename_fields():
    """A kind is a filename component, so its syntax is narrower than a name's.

    Entry-point names keep the hyphen -- they never enter a filename. Kinds do,
    at index 1 of ``{stamp}-{kind}-{instance_id}[-{label}]``, so a hyphen there
    shifts the id out from under ``split("-")[2]``.
    """
    validate_name("my-plugin", label="entry-point name")  # still legal
    with pytest.raises(PluginLoadError, match="must match"):
        validate_kind("my-plugin")
    validate_kind("my_plugin")  # underscore is the replacement


@pytest.mark.parametrize("instance_id", ["foo-bar", "Abc123", "a/b", "", "a.b", "ssh:host"])
def test_bad_instance_ids_are_refused(instance_id):
    with pytest.raises(ValueError, match="must match"):
        validate_instance_id(instance_id)


@pytest.mark.parametrize("instance_id", ["0f3ab19c22d4", "abc123", "a", "a_b"])
def test_good_instance_ids_are_accepted(instance_id):
    validate_instance_id(instance_id)


@pytest.mark.parametrize(
    ("kind", "instance_id", "label"),
    [
        ("refkind", "0f3ab19c22d4", None),  # pragma: allowlist secret (fake uuid4 hex fixture)
        ("my_plugin", "abc123", "some-repo-with-hyphens"),
        ("chromium", "4f3a2b1c0d9e", "octowright"),  # pragma: allowlist secret (fake uuid4 hex fixture)
    ],
)
def test_the_constraint_makes_the_recording_name_parser_exact(kind, instance_id, label):
    """The whole point, pinned end to end.

    This is the invariant the two regexes exist to protect: with a hyphen-free
    kind and id, ``instance_id_from_recording_name`` recovers the id exactly --
    even when the LABEL contains hyphens, because the label sits at index 3 and
    later, where extra fields are harmless.
    """
    from pathlib import Path

    from octowright.http.artifacts import instance_id_from_recording_name
    from octowright.recorder import new_log_path

    validate_instance_id(instance_id)
    stem = new_log_path(Path("/tmp/recordings"), instance_id, label, kind).stem
    assert instance_id_from_recording_name(stem) == instance_id, stem
