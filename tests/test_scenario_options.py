# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``Participant.options`` -- the opaque, kind-owned settings dict that
replaced ten terminal-only dataclass fields.

Terminal-specific coverage (connector_type defaults/validation, cols/rows/port
int-and-bool-trap checks, resolve_participant shapes) moved to
packages/octowright-terminal/tests/test_scenario_adapter.py in step 5's core
cleanup -- core no longer validates any plugin kind's own options, so those
assertions belong with the plugin that owns them. What's left here is
strictly kind-agnostic: the dataclass default and the generic YAML shape
check.
"""

from __future__ import annotations

import pytest

from octowright.scenarios import Participant


def test_participant_defaults_options_to_an_empty_dict():
    p = Participant(persona="tanuki-tim", kind="chromium", role="player")
    assert p.options == {}, "never None -- callers index it without a guard"


def test_yaml_rejects_a_non_mapping_options():
    from octowright.scenarios import load_yaml_scenario

    with pytest.raises(ValueError, match="options must be a mapping"):
        load_yaml_scenario(
            "name: demo\nparticipants:\n  - persona: t\n    kind: chromium\n    role: player\n    options: nope\n",
            "demo",
        )
