# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest
import yaml

from octowright.scenarios import load_yaml_scenario


def test_yaml_participant_missing_required_key_raises_value_error() -> None:
    content = yaml.safe_dump(
        {
            "name": "bad",
            "participants": [{"persona": "a", "role": "player"}],
        }
    )

    with pytest.raises(ValueError, match=r"participants\[0\].*kind"):
        load_yaml_scenario(content, "bad")


def test_yaml_participant_must_be_mapping() -> None:
    content = yaml.safe_dump(
        {
            "name": "bad",
            "participants": ["not-a-mapping"],
        }
    )

    with pytest.raises(ValueError, match=r"participants\[0\].*mapping"):
        load_yaml_scenario(content, "bad")


def test_yaml_participant_startup_macros_must_be_list_of_strings() -> None:
    content = yaml.safe_dump(
        {
            "name": "bad",
            "participants": [{"persona": "a", "kind": "webkit", "startup_macros": "login"}],
        }
    )

    with pytest.raises(ValueError, match=r"participants\[0\].*startup_macros.*list of strings"):
        load_yaml_scenario(content, "bad")


def test_yaml_participant_viewports_must_be_integers_not_booleans() -> None:
    content = yaml.safe_dump(
        {
            "name": "bad",
            "participants": [{"persona": "a", "kind": "webkit", "viewport_w": True}],
        }
    )

    with pytest.raises(ValueError, match=r"participants\[0\].*viewport_w.*integer"):
        load_yaml_scenario(content, "bad")


def test_yaml_participant_flags_must_be_booleans() -> None:
    content = yaml.safe_dump(
        {
            "name": "bad",
            "participants": [{"persona": "a", "kind": "webkit", "trace": "yes"}],
        }
    )

    with pytest.raises(ValueError, match=r"participants\[0\].*trace.*boolean"):
        load_yaml_scenario(content, "bad")


def test_yaml_fixtures_reject_unknown_keys() -> None:
    content = yaml.safe_dump(
        {
            "name": "bad",
            "participants": [{"persona": "a", "kind": "webkit"}],
            "fixtures": {"dialog_policy": "dismiss", "legacy_toggle": True},
        }
    )

    with pytest.raises(ValueError, match=r"fixtures.*unknown.*legacy_toggle"):
        load_yaml_scenario(content, "bad")


def test_yaml_fixture_dialog_policy_must_be_supported_policy() -> None:
    content = yaml.safe_dump(
        {
            "name": "bad",
            "participants": [{"persona": "a", "kind": "webkit"}],
            "fixtures": {"dialog_policy": "ignore"},
        }
    )

    with pytest.raises(ValueError, match=r"fixtures\.dialog_policy.*accept.*dismiss.*manual"):
        load_yaml_scenario(content, "bad")


def test_yaml_fixture_mock_routes_must_be_valid_route_specs() -> None:
    content = yaml.safe_dump(
        {
            "name": "bad",
            "participants": [{"persona": "a", "kind": "webkit"}],
            "fixtures": {"mock_routes": [{"pattern": "", "status": True}]},
        }
    )

    with pytest.raises(ValueError, match=r"fixtures\.mock_routes\[0\]\.pattern.*non-empty string"):
        load_yaml_scenario(content, "bad")


def test_yaml_fixture_mock_route_status_must_be_http_status() -> None:
    content = yaml.safe_dump(
        {
            "name": "bad",
            "participants": [{"persona": "a", "kind": "webkit"}],
            "fixtures": {"mock_routes": [{"pattern": "**/api", "status": 999}]},
        }
    )

    with pytest.raises(ValueError, match=r"fixtures\.mock_routes\[0\]\.status.*100.*599"):
        load_yaml_scenario(content, "bad")
