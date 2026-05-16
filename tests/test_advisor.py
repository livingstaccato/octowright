# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from octowright import advisor
from octowright.server.profiles import ALWAYS_ON_TOOLS, PROFILES

_JSON_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(),
    lambda children: st.lists(children, max_size=6) | st.dictionaries(st.text(), children, max_size=6),
    max_leaves=20,
)
_VALID_PREFERENCES = {"yes", "no", "automatic"}
_VALID_SUGGESTION_TYPES = {"macro_candidate", "profile_change"}
_HYPOTHESIS_SETTINGS = settings(max_examples=80, suppress_health_check=[HealthCheck.function_scoped_fixture])
_HYPOTHESIS_SETTINGS_SMALL = settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])


def test_default_state_has_per_type_preferences(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)

    state = advisor.load_state()

    assert state["preferences"] == {
        "macro_candidate": "yes",
        "profile_change": "yes",
    }
    assert state["tool_usage"] == []
    assert state["macro_observations"] == []


def test_state_round_trips_preferences_and_usage(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)

    advisor.set_preference("profile_change", "automatic")
    advisor.record_tool_call("browser_launch")
    advisor.record_tool_call("browser_click")

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["preferences"]["profile_change"] == "automatic"
    assert [event["tool"] for event in loaded["tool_usage"]] == ["browser_launch", "browser_click"]
    assert [event["profile"] for event in loaded["tool_usage"]] == ["core", "core"]


def test_always_on_tools_are_not_counted_as_usage(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)

    advisor.record_tool_call("octowright_status")
    advisor.record_tool_call("octowright_advisor_status")

    assert advisor.load_state()["tool_usage"] == []


def test_load_state_skips_invalid_nested_entries(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    path.write_text(
        json.dumps(
            {
                "preferences": {
                    "macro_candidate": "bogus",
                    "profile_change": "no",
                },
                "tool_usage": [
                    None,
                    {},
                    {"tool": ""},
                    {"tool": "browser_launch", "profile": 123},
                ],
                "macro_observations": [
                    None,
                    {},
                    {"signature": ""},
                    {"signature": "login", "source": 123, "summary": "password=abc123"},
                ],
                "cooldowns": {
                    "valid": 1,
                    "invalid": "soon",
                },
            }
        ),
        encoding="utf-8",
    )

    state = advisor.load_state()

    assert state["preferences"] == {
        "macro_candidate": "yes",
        "profile_change": "no",
    }
    assert state["tool_usage"] == [
        {
            "ts": 0.0,
            "tool": "browser_launch",
            "profile": None,
        }
    ]
    assert state["macro_observations"] == [
        {
            "ts": 0.0,
            "source": "123",
            "signature": "login",
            "summary": "password=[redacted]",
        }
    ]
    assert state["cooldowns"] == {"valid": 1.0}


def test_reset_state_persists_default_state(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    advisor.set_preference("macro_candidate", "no")
    advisor.record_tool_call("browser_launch")

    state = advisor.reset_state()

    assert state["preferences"]["macro_candidate"] == "yes"
    assert state["tool_usage"] == []
    assert advisor.load_state() == state


def test_core_only_usage_suggests_reducing_full_surface(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "")
    for tool_name in ("browser_launch", "browser_click", "browser_fill", "browser_close"):
        advisor.record_tool_call(tool_name)

    suggestions = advisor.current_suggestions()

    assert suggestions == [
        {
            "id": "profile-change-core",
            "type": "profile_change",
            "reason": "Recent Octowright usage only touched core browser-driving tools.",
            "recommended_action": "Restart Octowright with OCTOWRIGHT_PROFILE=core.",
            "choices": ["yes", "no", "automatic"],
            "profile": "core",
            "mode": "prompt",
        }
    ]


def test_macro_usage_suggests_expanding_core_profile(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "core")
    advisor.record_tool_call("browser_launch")
    advisor.record_tool_call("macro_run")

    suggestions = advisor.current_suggestions()

    assert suggestions[0]["id"] == "profile-change-core-macros"
    assert suggestions[0]["profile"] == "core,macros"
    assert suggestions[0]["recommended_action"] == "Restart Octowright with OCTOWRIGHT_PROFILE=core,macros."


def test_full_surface_non_core_usage_does_not_suggest_profile_reduction(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "")
    advisor.record_tool_call("macro_run")

    assert advisor.current_suggestions() == []


def test_profile_automatic_mode_marks_profile_suggestion_auto_apply(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "")
    advisor.set_preference("profile_change", "automatic")
    advisor.record_tool_call("browser_launch")
    advisor.record_tool_call("browser_click")

    suggestions = advisor.current_suggestions()

    assert suggestions[0]["mode"] == "auto_apply"


def test_macro_automatic_mode_never_auto_saves(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    advisor.set_preference("macro_candidate", "automatic")
    advisor.record_macro_observation(
        source="llm",
        signature="login-flow",
        summary="Repeated login flow",
    )
    advisor.record_macro_observation(
        source="server",
        signature="login-flow",
        summary="Repeated login flow",
    )

    suggestions = advisor.current_suggestions()

    assert suggestions == [
        {
            "id": "macro-candidate-login-flow",
            "type": "macro_candidate",
            "reason": "Repeated login flow",
            "recommended_action": "Ask the user whether to save this repeated workflow as a macro.",
            "choices": ["yes", "no", "automatic"],
            "mode": "prompt",
            "source": "mixed",
        }
    ]


def test_no_preference_suppresses_suggestion_type(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "")
    advisor.set_preference("profile_change", "no")
    advisor.record_tool_call("browser_launch")
    advisor.record_tool_call("browser_click")

    assert advisor.current_suggestions() == []


def test_invalid_preference_value_raises(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)

    with pytest.raises(ValueError):
        advisor.set_preference("profile_change", "sometimes")


def test_matching_active_profile_does_not_suggest_profile_change(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "core")
    advisor.record_tool_call("browser_launch")
    advisor.record_tool_call("browser_click")

    assert advisor.current_suggestions() == []


def test_macro_preference_no_suppresses_macro_suggestion(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    advisor.set_preference("macro_candidate", "no")
    advisor.record_macro_observation(source="llm", signature="login-flow", summary="Repeated login flow")
    advisor.record_macro_observation(source="server", signature="login-flow", summary="Repeated login flow")

    assert advisor.current_suggestions() == []


def test_status_reports_usage_summary(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    advisor.record_tool_call("browser_launch")
    advisor.record_tool_call("unknown_tool")
    advisor.record_macro_observation(source="llm", signature="login-flow", summary="Repeated login flow")

    status = advisor.status()

    assert status["name"] == "Octowright Advisor"
    assert status["usage"] == {
        "tool_calls": 2,
        "profiles": {"core": 1},
        "macro_observations": 1,
    }


@_HYPOTHESIS_SETTINGS
@given(raw=_JSON_VALUES)
def test_load_state_normalises_arbitrary_persisted_json(tmp_path: Path, monkeypatch, raw) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    path.write_text(json.dumps(raw), encoding="utf-8")

    state = advisor.load_state()

    assert set(state["preferences"]) == _VALID_SUGGESTION_TYPES
    assert set(state["preferences"].values()).issubset(_VALID_PREFERENCES)
    assert len(state["tool_usage"]) <= 200
    assert len(state["macro_observations"]) <= 100
    for event in state["tool_usage"]:
        assert isinstance(event["ts"], float)
        assert event["tool"]
        assert isinstance(event["tool"], str)
        assert event["profile"] is None or isinstance(event["profile"], str)
    for observation in state["macro_observations"]:
        assert isinstance(observation["ts"], float)
        assert observation["signature"]
        assert isinstance(observation["summary"], str)


@_HYPOTHESIS_SETTINGS
@given(tool_name=st.text())
def test_record_tool_call_maps_arbitrary_tool_names(tmp_path: Path, monkeypatch, tool_name: str) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    path.unlink(missing_ok=True)

    advisor.record_tool_call(tool_name)

    state = advisor.load_state()
    if not tool_name:
        assert state["tool_usage"] == []
        assert not path.exists()
        return
    if tool_name in ALWAYS_ON_TOOLS:
        assert state["tool_usage"] == []
        return

    profile_index = {tool: profile for profile, tools in PROFILES.items() for tool in tools}
    assert state["tool_usage"] == [
        {
            "ts": state["tool_usage"][0]["ts"],
            "tool": tool_name,
            "profile": profile_index.get(tool_name),
        }
    ]


@_HYPOTHESIS_SETTINGS
@given(suggestion_type=st.text(), preference=st.text())
def test_set_preference_rejects_invalid_values(
    tmp_path: Path, monkeypatch, suggestion_type: str, preference: str
) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    path.unlink(missing_ok=True)

    if suggestion_type in _VALID_SUGGESTION_TYPES and preference in _VALID_PREFERENCES:
        advisor.set_preference(suggestion_type, preference)
        assert advisor.load_state()["preferences"][suggestion_type] == preference
        return

    with pytest.raises(ValueError):
        advisor.set_preference(suggestion_type, preference)


@_HYPOTHESIS_SETTINGS_SMALL
@given(
    secret_key=st.sampled_from(["password", "passwd", "pwd", "token", "secret", "api_key", "api-key"]),
    secret_value=st.text(
        alphabet=st.characters(blacklist_characters=" \t\r\n,;", blacklist_categories=("Cc", "Cs", "Zs")),
        min_size=1,
    ),
)
def test_macro_observation_redacts_secretish_values(
    tmp_path: Path,
    monkeypatch,
    secret_key: str,
    secret_value: str,
) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    path.unlink(missing_ok=True)

    advisor.record_macro_observation(
        source="llm",
        signature="secret-flow",
        summary=f"Repeated flow with {secret_key}:{secret_value}",
    )

    summary = advisor.load_state()["macro_observations"][0]["summary"]
    assert summary == f"Repeated flow with {secret_key}=[redacted]"


@_HYPOTHESIS_SETTINGS
@given(signature=st.text(min_size=1))
def test_macro_candidate_ids_are_stable_safe_slugs(tmp_path: Path, monkeypatch, signature: str) -> None:
    path = tmp_path / "advisor.json"
    monkeypatch.setattr(advisor, "ADVISOR_STATE_PATH", path)
    path.unlink(missing_ok=True)

    advisor.record_macro_observation(source="llm", signature=signature, summary="Repeated flow")
    advisor.record_macro_observation(source="server", signature=signature, summary="Repeated flow")

    suggestion_id = advisor.current_suggestions()[0]["id"]
    slug = suggestion_id.removeprefix("macro-candidate-")
    assert suggestion_id.startswith("macro-candidate-")
    assert slug
    assert set(slug).issubset(set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"))
