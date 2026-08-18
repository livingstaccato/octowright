# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A scenario's participant role must not be stamped over an action's ARIA role.

``ScenarioPool.tail`` merges every participant's JSONL into one stream and
labels each event with who produced it. It used to write that label to the key
``role`` — the same key ``click_by``/``fill_by``/``click``/``fill`` use for the
ARIA role they match on.

Two things went wrong at once, and neither was visible:

* the recorded ARIA role was overwritten, so a ``click_by`` that matched
  ``role="button"`` came back claiming ``role="player"``;
* an action that never had an ARIA role at all (a plain ``click`` on a CSS
  selector) had one injected.

``strip_non_aria_noise`` cannot undo either — it returns early for exactly the
semantic actions, because for them ``role`` IS the locator. And ``lint_macro``
stays silent, because ``role`` is a legitimate field on those actions. So a
macro built from a scenario tail replays against ``get_by_role("player")``:
either a hard ``ValueError``/no-match, or a silent fall-through to the CSS
selector that makes the ARIA half dead weight.

``persona`` was patched by adding it to ``RECORDING_NOISE_KEYS``. That works
only because no session method takes a ``persona`` argument. ``role`` cannot be
fixed that way without breaking every legitimate ARIA click, so it is fixed at
the source: the label is written to ``scenario_role``, which collides with
nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from octowright.macros.substitution import RECORDING_NOISE_KEYS, action_kwargs, strip_non_aria_noise
from octowright.scenarios_pool import ScenarioPool


def _tail_one(tmp_path: Path, event: dict[str, Any]) -> dict[str, Any]:
    log = tmp_path / "a.jsonl"
    log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    pool = ScenarioPool()
    pool._live["s-1"] = type(  # type: ignore[attr-defined]
        "Live",
        (),
        {
            "participants": [
                {"instance_id": "i-a", "persona": "cosmo", "role": "player", "log_path": str(log)},
            ]
        },
    )()
    return dict(pool.tail(scenario_id="s-1")["events"][0])


def test_tail_does_not_overwrite_a_recorded_aria_role(tmp_path: Path) -> None:
    entry = _tail_one(
        tmp_path,
        {"action": "click_by", "role": "button", "role_name": "Save", "ts": "t"},
    )
    assert entry["role"] == "button"
    assert entry["scenario_role"] == "player"
    assert entry["persona"] == "cosmo"


def test_tail_does_not_inject_a_role_into_an_action_that_had_none(tmp_path: Path) -> None:
    entry = _tail_one(tmp_path, {"action": "click", "selector": "#buy", "ts": "t"})
    assert "role" not in entry
    assert entry["scenario_role"] == "player"


def test_scenario_labels_are_stripped_before_replay_dispatch() -> None:
    """Both labels must be gone from the kwargs, for semantic actions too."""
    assert "scenario_role" in RECORDING_NOISE_KEYS
    assert "persona" in RECORDING_NOISE_KEYS

    event = {
        "action": "click_by",
        "role": "button",
        "role_name": "Save",
        "ts": "t",
        "instance_id": "i-a",
        "persona": "cosmo",
        "scenario_role": "player",
    }
    kwargs = strip_non_aria_noise("click_by", action_kwargs(event))
    assert kwargs == {"role": "button", "role_name": "Save"}


def test_plain_click_keeps_only_its_selector() -> None:
    event = {
        "action": "click",
        "selector": "#buy",
        "ts": "t",
        "instance_id": "i-a",
        "persona": "cosmo",
        "scenario_role": "player",
    }
    assert strip_non_aria_noise("click", action_kwargs(event)) == {"selector": "#buy"}
