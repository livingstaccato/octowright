# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Octowright Advisor: local adaptive suggestions for agents.

V1 keeps the Advisor intentionally local and deterministic. It records small
tool-usage and macro-observation summaries, then emits typed suggestion payloads
that MCP tools can include in responses.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from typing import Any, Literal, TypedDict

from octowright.defaults import ADVISOR_STATE_PATH
from octowright.server.profiles import ALWAYS_ON_TOOLS, PROFILES

SuggestionType = Literal["macro_candidate", "profile_change"]
Preference = Literal["yes", "no", "automatic"]

_PREFERENCES: tuple[Preference, ...] = ("yes", "no", "automatic")
_PROFILE_ORDER = ("core", "advanced", "macros", "scenarios", "personas")
_MAX_TOOL_USAGE = 200
_MAX_MACRO_OBSERVATIONS = 100
_PROFILE_TOOL_INDEX = {tool: profile for profile, tools in PROFILES.items() for tool in tools}


class ToolUsageEvent(TypedDict):
    ts: float
    tool: str
    profile: str | None


class MacroObservation(TypedDict):
    ts: float
    source: str
    signature: str
    summary: str


class AdvisorState(TypedDict):
    preferences: dict[SuggestionType, Preference]
    tool_usage: list[ToolUsageEvent]
    macro_observations: list[MacroObservation]
    cooldowns: dict[str, float]


class AdvisorSuggestion(TypedDict, total=False):
    id: str
    type: SuggestionType
    reason: str
    recommended_action: str
    choices: list[str]
    mode: str
    profile: str
    source: str


def _default_state() -> AdvisorState:
    return {
        "preferences": {
            "macro_candidate": "yes",
            "profile_change": "yes",
        },
        "tool_usage": [],
        "macro_observations": [],
        "cooldowns": {},
    }


def _normalise_state(raw: Any) -> AdvisorState:
    state = _default_state()
    if not isinstance(raw, dict):
        return state

    _normalise_preferences(raw.get("preferences"), state)
    state["tool_usage"] = _normalise_tool_usage(raw.get("tool_usage"))
    state["macro_observations"] = _normalise_macro_observations(raw.get("macro_observations"))
    state["cooldowns"] = _normalise_cooldowns(raw.get("cooldowns"))
    return state


def _normalise_preferences(raw_preferences: Any, state: AdvisorState) -> None:
    if not isinstance(raw_preferences, dict):
        return
    for key in ("macro_candidate", "profile_change"):
        value = raw_preferences.get(key)
        if value in _PREFERENCES:
            state["preferences"][key] = value


def _normalise_tool_usage(raw_tool_usage: Any) -> list[ToolUsageEvent]:
    if not isinstance(raw_tool_usage, list):
        return []
    tool_usage: list[ToolUsageEvent] = []
    for item in raw_tool_usage:
        event = _normalise_tool_usage_event(item)
        if event is not None:
            tool_usage.append(event)
    return tool_usage[-_MAX_TOOL_USAGE:]


def _normalise_tool_usage_event(item: Any) -> ToolUsageEvent | None:
    if not isinstance(item, dict) or not item.get("tool"):
        return None
    profile = item.get("profile")
    return {
        "ts": float(item.get("ts", 0.0)),
        "tool": str(item.get("tool", "")),
        "profile": profile if isinstance(profile, str) else None,
    }


def _normalise_macro_observations(raw_observations: Any) -> list[MacroObservation]:
    if not isinstance(raw_observations, list):
        return []
    observations: list[MacroObservation] = []
    for item in raw_observations:
        observation = _normalise_macro_observation(item)
        if observation is not None:
            observations.append(observation)
    return observations[-_MAX_MACRO_OBSERVATIONS:]


def _normalise_macro_observation(item: Any) -> MacroObservation | None:
    if not isinstance(item, dict) or not item.get("signature"):
        return None
    return {
        "ts": float(item.get("ts", 0.0)),
        "source": str(item.get("source", "unknown")),
        "signature": str(item.get("signature", "")),
        "summary": _redact_summary(str(item.get("summary", ""))),
    }


def _normalise_cooldowns(raw_cooldowns: Any) -> dict[str, float]:
    if not isinstance(raw_cooldowns, dict):
        return {}
    return {str(key): float(value) for key, value in raw_cooldowns.items() if isinstance(value, int | float)}


def load_state() -> AdvisorState:
    try:
        raw = json.loads(ADVISOR_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    return _normalise_state(raw)


def save_state(state: AdvisorState) -> None:
    ADVISOR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ADVISOR_STATE_PATH.with_suffix(ADVISOR_STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(ADVISOR_STATE_PATH)


def reset_state() -> AdvisorState:
    state = _default_state()
    save_state(state)
    return state


def set_preference(suggestion_type: SuggestionType, preference: Preference) -> AdvisorState:
    if suggestion_type not in ("macro_candidate", "profile_change"):
        raise ValueError("suggestion_type must be macro_candidate or profile_change")
    if preference not in _PREFERENCES:
        raise ValueError("preference must be yes, no, or automatic")
    state = load_state()
    state["preferences"][suggestion_type] = preference
    save_state(state)
    return state


def record_tool_call(tool_name: str) -> None:
    if not tool_name or tool_name in ALWAYS_ON_TOOLS:
        return
    state = load_state()
    state["tool_usage"].append(
        {
            "ts": time.time(),
            "tool": tool_name,
            "profile": _PROFILE_TOOL_INDEX.get(tool_name),
        }
    )
    state["tool_usage"] = state["tool_usage"][-_MAX_TOOL_USAGE:]
    save_state(state)


def record_macro_observation(*, source: str, signature: str, summary: str) -> None:
    state = load_state()
    state["macro_observations"].append(
        {
            "ts": time.time(),
            "source": source,
            "signature": signature,
            "summary": _redact_summary(summary),
        }
    )
    state["macro_observations"] = state["macro_observations"][-_MAX_MACRO_OBSERVATIONS:]
    save_state(state)


def current_suggestions() -> list[AdvisorSuggestion]:
    state = load_state()
    suggestions: list[AdvisorSuggestion] = []
    macro = _macro_suggestion(state)
    if macro is not None:
        suggestions.append(macro)
    profile = _profile_suggestion(state)
    if profile is not None:
        suggestions.append(profile)
    return suggestions


def status() -> dict[str, Any]:
    state = load_state()
    return {
        "name": "Octowright Advisor",
        "preferences": state["preferences"],
        "usage": _usage_summary(state),
        "suggestions": current_suggestions(),
    }


def _active_profile_names() -> set[str] | None:
    from octowright.defaults import active_profile_raw

    raw = active_profile_raw()
    if not raw or raw.lower() == "all":
        return None
    return {name.strip() for name in raw.split(",") if name.strip() in PROFILES}


def _usage_summary(state: AdvisorState) -> dict[str, Any]:
    profile_counts = Counter(event["profile"] for event in state["tool_usage"] if event["profile"])
    return {
        "tool_calls": len(state["tool_usage"]),
        "profiles": dict(sorted(profile_counts.items())),
        "macro_observations": len(state["macro_observations"]),
    }


def _profile_suggestion(state: AdvisorState) -> AdvisorSuggestion | None:
    if state["preferences"]["profile_change"] == "no":
        return None

    used_profiles = {event["profile"] for event in state["tool_usage"] if event["profile"]}
    if not used_profiles:
        return None

    active = _active_profile_names()
    mode = "auto_apply" if state["preferences"]["profile_change"] == "automatic" else "prompt"

    if used_profiles == {"core"} and active != {"core"}:
        return _core_profile_suggestion(mode)

    if active is None:
        return None

    missing = used_profiles - active
    if not missing:
        return None
    return _expanded_profile_suggestion(active, missing, mode)


def _core_profile_suggestion(mode: str) -> AdvisorSuggestion:
    return {
        "id": "profile-change-core",
        "type": "profile_change",
        "reason": "Recent Octowright usage only touched core browser-driving tools.",
        "recommended_action": "Restart Octowright with OCTOWRIGHT_PROFILE=core.",
        "choices": ["yes", "no", "automatic"],
        "profile": "core",
        "mode": mode,
    }


def _expanded_profile_suggestion(active: set[str], missing: set[str], mode: str) -> AdvisorSuggestion:
    target_profiles = [name for name in _PROFILE_ORDER if name in (active | missing)]
    profile_spec = ",".join(target_profiles)
    missing_label = ", ".join(name for name in _PROFILE_ORDER if name in missing)
    return {
        "id": f"profile-change-{profile_spec.replace(',', '-')}",
        "type": "profile_change",
        "reason": f"Recent Octowright usage touched {missing_label} tools outside the active profile.",
        "recommended_action": f"Restart Octowright with OCTOWRIGHT_PROFILE={profile_spec}.",
        "choices": ["yes", "no", "automatic"],
        "profile": profile_spec,
        "mode": mode,
    }


def _macro_suggestion(state: AdvisorState) -> AdvisorSuggestion | None:
    if state["preferences"]["macro_candidate"] == "no":
        return None
    by_signature: dict[str, list[MacroObservation]] = defaultdict(list)
    for observation in state["macro_observations"]:
        by_signature[observation["signature"]].append(observation)
    repeated = [(signature, items) for signature, items in by_signature.items() if len(items) >= 2]
    if not repeated:
        return None
    signature, items = max(repeated, key=lambda entry: max(item["ts"] for item in entry[1]))
    sources = {item["source"] for item in items}
    source = "mixed" if len(sources) > 1 else next(iter(sources))
    summary = items[-1]["summary"] or f"Repeated workflow {signature}"
    return {
        "id": f"macro-candidate-{_slug(signature)}",
        "type": "macro_candidate",
        "reason": summary,
        "recommended_action": "Ask the user whether to save this repeated workflow as a macro.",
        "choices": ["yes", "no", "automatic"],
        "mode": "prompt",
        "source": source,
    }


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return cleaned or "workflow"


_SECRETISH = re.compile(r"(?i)(password|passwd|pwd|token|secret|api[_-]?key)\s*[:=]\s*([^\s,;]+)")


def _redact_summary(summary: str) -> str:
    return _SECRETISH.sub(r"\1=[redacted]", summary)
