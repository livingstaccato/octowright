# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_export_cli`` refuses a macro the script it would write cannot run.

The exported script dispatches only ``EXPORT_DISPATCH``'s kinds (plus the
lifecycle steps it skips). Live replay also runs ``if_selector``, ``try``,
``try_each`` and ``macro_call``, so a macro using one exported fine and then
died with "unsupported macro action in exported CLI" -- after every earlier
step had already run against the target. The export now names the step.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from octowright.artifacts.script_export_actions import EXPORT_SKIPPED, exported_action_kinds, unrunnable_steps


def _reload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "recordings"))
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))

    import octowright.artifacts.paths as artifact_paths
    import octowright.defaults as defaults
    import octowright.macros.artifacts as macro_artifacts
    import octowright.macros.storage as storage

    importlib.reload(defaults)
    importlib.reload(storage)
    importlib.reload(artifact_paths)
    importlib.reload(macro_artifacts)
    return storage, macro_artifacts


@pytest.mark.parametrize("kind", ["if_selector", "try", "try_each", "macro_call", "not_a_kind"])
def test_export_names_the_step_the_script_cannot_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(
        name="branchy",
        macro={
            "name": "branchy",
            "actions": [
                {"action": "navigate", "url": "https://example.test/"},
                {"action": kind, "selector": "#x", "then": []},
            ],
        },
    )

    with pytest.raises(ValueError, match=rf"step 1 \({kind}\)"):
        macro_artifacts.export_macro_cli(name="branchy")

    assert not list((tmp_path / "recordings").rglob("*.py")), "no script is written for a refused macro"


def test_every_dispatched_and_skipped_kind_is_runnable() -> None:
    actions = [{"action": kind} for kind in sorted(exported_action_kinds() | EXPORT_SKIPPED)]

    assert unrunnable_steps(actions) == []


def test_a_step_without_a_kind_is_unrunnable() -> None:
    assert unrunnable_steps([{"selector": "#x"}, "not a step"]) == [(0, None), (1, None)]


def test_the_script_skips_exactly_the_exported_skip_set() -> None:
    from octowright.artifacts.script_export import render_macro_cli

    script = render_macro_cli(name="m", macro={"actions": []}, include_evidence=False)
    namespace: dict[str, object] = {}
    line = next(line for line in script.splitlines() if line.startswith("_LIFECYCLE_SKIP ="))
    exec(line, namespace)

    assert namespace["_LIFECYCLE_SKIP"] == set(EXPORT_SKIPPED)
