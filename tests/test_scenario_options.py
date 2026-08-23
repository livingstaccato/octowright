# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.scenarios import Participant, resolve_terminal_launch


def test_participant_defaults_options_to_an_empty_dict():
    p = Participant(persona="tanuki-tim", kind="chromium", role="player")
    assert p.options == {}, "never None -- callers index it without a guard"


def test_participant_no_longer_carries_terminal_fields():
    """The ten terminal fields are gone; a plugin's settings live in options."""
    p = Participant(persona="tanuki-tim", kind="terminal", role="monitor")
    for gone in (
        "connector_type",
        "command",
        "cols",
        "rows",
        "host",
        "port",
        "user",
        "key_path",
        "known_hosts",
        "insecure_no_host_check",
    ):
        assert not hasattr(p, gone), f"{gone} must move under options"


def test_yaml_parses_options_through_opaquely():
    from octowright.scenarios import load_yaml_scenario

    spec = load_yaml_scenario(
        "name: demo\n"
        "participants:\n"
        "  - persona: tanuki-tim\n"
        "    kind: terminal\n"
        "    role: monitor\n"
        "    options:\n"
        "      connector_type: pty\n"
        "      command: /bin/zsh\n"
        "      cols: 100\n",
        "demo",
    )
    assert spec.participants[0].options == {"connector_type": "pty", "command": "/bin/zsh", "cols": 100}


def test_yaml_rejects_a_non_mapping_options():
    from octowright.scenarios import load_yaml_scenario

    with pytest.raises(ValueError, match="options must be a mapping"):
        load_yaml_scenario(
            "name: demo\nparticipants:\n  - persona: t\n    kind: chromium\n    role: player\n    options: nope\n",
            "demo",
        )


def test_terminal_launch_reads_connector_type_from_options():
    p = Participant(
        persona="tanuki-tim",
        kind="terminal",
        role="monitor",
        options={"command": "/bin/zsh", "cols": 100, "rows": 40},
    )
    launch = resolve_terminal_launch(p)
    assert launch["kind"] == "pty", "omitted connector_type still defaults to pty"
    assert launch["profile"] == "tanuki-tim"
    assert launch["connector_config"]["command"] == "/bin/zsh"


def test_terminal_launch_honours_an_explicit_connector_type():
    p = Participant(
        persona="tanuki-tim",
        kind="terminal",
        role="monitor",
        options={"connector_type": "ssh", "host": "box.test", "user": "tim", "known_hosts": "/tmp/kh"},
    )
    launch = resolve_terminal_launch(p)
    assert launch["kind"] == "ssh"
    assert launch["connector_config"]["host"] == "box.test"
    assert launch["connector_config"]["username"] == "tim"


def test_an_unsupported_connector_type_is_still_refused():
    from octowright.scenarios import Scenario, _validate_participant_kind

    p = Participant(persona="t", kind="terminal", role="monitor", options={"connector_type": "carrier-pigeon"})
    s = Scenario(name="demo", participants=[p])
    with pytest.raises(ValueError, match="unsupported connector_type"):
        _validate_participant_kind(s, p)


def test_a_terminal_participant_still_cannot_declare_startup_macros():
    from octowright.scenarios import Scenario, _validate_participant_kind

    p = Participant(persona="t", kind="terminal", role="monitor", startup_macros=["login"])
    s = Scenario(name="demo", participants=[p])
    with pytest.raises(ValueError, match="startup_macros"):
        _validate_participant_kind(s, p)
