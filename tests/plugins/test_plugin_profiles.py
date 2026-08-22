# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.server import profiles


@pytest.fixture(autouse=True)
def _clean_plugin_profiles():
    profiles.reset_plugin_profiles()
    yield
    profiles.reset_plugin_profiles()


def test_plugin_profile_widens_the_allowed_set():
    profiles.register_plugin_profile("refkinds", ["refkind_launch", "refkind_close"])
    allowed = profiles.build_allowed_set("refkinds")
    assert {"refkind_launch", "refkind_close"} <= allowed
    assert allowed >= profiles.ALWAYS_ON_TOOLS


def test_plugin_profile_does_not_trigger_unknown_diagnostics(caplog):
    profiles.register_plugin_profile("refkinds", ["refkind_launch"])
    with caplog.at_level("WARNING"):
        profiles.build_allowed_set("refkinds")
    messages = [record.getMessage() for record in caplog.records]
    assert not any("profile.unknown" in message for message in messages)
    assert not any("profile.all_unknown" in message for message in messages)


def test_a_genuinely_unknown_profile_still_warns(caplog):
    with caplog.at_level("WARNING"):
        profiles.build_allowed_set("nosuchprofile")
    messages = [record.getMessage() for record in caplog.records]
    assert any("profile.unknown" in message for message in messages)


def test_plugin_profile_may_not_shadow_a_core_profile():
    with pytest.raises(ValueError, match="core profile"):
        profiles.register_plugin_profile("core", ["refkind_launch"])


def test_registered_names_are_listed():
    profiles.register_plugin_profile("refkinds", ["refkind_launch"])
    assert profiles.plugin_profile_names() == ["refkinds"]
