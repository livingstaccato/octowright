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


# ─── mock-route accept side ──────────────────────────────────────────────────
#
# The tests above assert only that bad specs are REJECTED. Nothing asserted
# that a good one is accepted, and a rejection test cannot see the difference
# between "this key is allowed" and "this key was never considered" -- so
# mutation testing found the allowed-key set and both range boundaries
# completely uncovered. Mutating `100 <= value` to `101 <=`, or dropping
# `"headers"` from the allowed set, changed real behaviour and no test noticed.


def _scenario_with_route(route: dict[str, object]) -> str:
    return yaml.safe_dump(
        {
            "name": "ok",
            "participants": [{"persona": "a", "kind": "webkit"}],
            "fixtures": {"mock_routes": [route]},
        }
    )


@pytest.mark.parametrize("status", [100, 599])
def test_mock_route_status_range_boundaries_are_accepted(status: int) -> None:
    """100 and 599 are inside the documented range.

    Only an accept-side assertion pins the boundary: a test that 999 is
    refused passes just as happily against `101 <= value <= 598`.
    """
    scenario = load_yaml_scenario(_scenario_with_route({"pattern": "**/api", "status": status}), "ok")

    assert scenario.fixtures["mock_routes"][0]["status"] == status


@pytest.mark.parametrize("status", [99, 600])
def test_mock_route_status_just_outside_the_range_is_refused(status: int) -> None:
    """The other half of the boundary, so neither edge can drift unnoticed."""
    with pytest.raises(ValueError, match=r"fixtures\.mock_routes\[0\]\.status.*100.*599"):
        load_yaml_scenario(_scenario_with_route({"pattern": "**/api", "status": status}), "ok")


def test_mock_route_accepts_every_documented_key() -> None:
    """Each allowed key must survive validation and reach the normalized spec.

    `_validate_mock_route` computes `extra` by subtracting a literal set, so a
    key missing from that set is reported as unknown and a valid scenario is
    refused. Asserting the normalized output also covers
    `_copy_optional_mock_route_fields` carrying each one through.
    """
    route = {
        "pattern": "**/api",
        "status": 201,
        "body": "{}",
        "content_type": "application/json",
        "headers": {"x-test": "1"},
    }

    normalized = load_yaml_scenario(_scenario_with_route(route), "ok").fixtures["mock_routes"][0]

    assert normalized == route


def test_mock_route_still_refuses_an_unknown_key() -> None:
    """The accept-side tests must not be satisfiable by dropping the check."""
    with pytest.raises(ValueError, match=r"fixtures\.mock_routes\[0\] unknown keys: \['nope'\]"):
        load_yaml_scenario(_scenario_with_route({"pattern": "**/api", "nope": 1}), "ok")


def test_every_optional_mock_route_field_round_trips() -> None:
    """All four optional fields must reach the normalized route, under their own keys.

    ``_copy_optional_mock_route_fields`` is four independent ``if "x" in route``
    copies, and the suite had assertions for the *validators* it calls but none
    for the copying itself. Every one of those keys appears twice -- once read
    from ``route``, once written to ``normalized`` -- so a mangled spelling on
    either side drops the field silently: the scenario still loads, the route
    still mocks, and it answers with the default instead of the 404 or the
    Content-Type the author wrote. Nothing raises, so nothing failed.

    Asserted together rather than one test per field because the defect is a
    per-field typo: a single populated route proves all four paths at once and
    a missing fifth field added later shows up here as an omission.
    """
    route = {
        "pattern": "**/api/orders",
        "status": 404,
        "body": '{"detail":"gone"}',
        "content_type": "application/problem+json",
        "headers": {"X-Trace": "abc123"},
    }

    scenario = load_yaml_scenario(_scenario_with_route(route), "ok")

    assert scenario.fixtures["mock_routes"][0] == route


def test_an_omitted_optional_mock_route_field_stays_absent() -> None:
    """The companion: absent must mean absent, not present-and-defaulted.

    Without this, dropping the ``if "status" in route`` guard entirely would
    pass the round-trip test above -- the key is present there. A route that
    sets no status must not acquire one, because ``mock_route`` distinguishes
    "leave the response alone" from "force a status", and a defaulted value
    silently converts a passthrough into an override.
    """
    scenario = load_yaml_scenario(_scenario_with_route({"pattern": "**/api"}), "ok")

    normalized = scenario.fixtures["mock_routes"][0]
    assert normalized == {"pattern": "**/api"}
    for field in ("status", "body", "content_type", "headers"):
        assert field not in normalized
