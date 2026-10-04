# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a called macro classifies is hidden everywhere the run reports it (#248).

``outer`` takes ``nat`` (no credential-like name) and passes it to ``inner``,
whose ``parameter_specs`` declare ``id`` sensitive. The value joins the run's
ledger when the call executes, but ``args_used`` (on success and on a failed
sequence step), the artifact manifest's ``parameters`` and the macro pill's
text were built from ``outer``'s view alone and carried it in the clear.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.macros import execution
from tests._macro_artifact_fixtures import _FakeSession, _reload, restore_reloaded_defaults
from tests.test_macro_parameter_specs import _absent, _install, _session

NAT = "Fixture-Not-A-Real-Secret-Nat-91x"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _restore() -> Any:
    yield
    restore_reloaded_defaults()


def _macros(inner_selector: str = "#row-{{id}}") -> dict[str, dict[str, Any]]:
    return {
        "outer": {
            "name": "outer",
            "parameters": ["nat"],
            "actions": [{"action": "macro_call", "name": "inner", "args": {"id": "{{nat}}"}}],
        },
        "inner": {
            "name": "inner",
            "parameters": ["id"],
            "parameter_specs": {"id": {"sensitive": True}},
            "actions": [
                {"action": "expect_text", "selector": "body", "text": "{{id}}"},
                {"action": "click", "selector": inner_selector},
            ],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [NAT, NAT.upper(), NAT.lower()])
async def test_a_successful_runs_args_used_hides_what_a_called_macro_classified(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _install(monkeypatch, _macros())
    result = await execution.run_macro(_session(), "outer", {"nat": value})
    assert result["args_used"] == {"nat": "<redacted>"}
    assert _absent(value, json.dumps(result))


@pytest.mark.asyncio
async def test_a_failed_sequence_steps_args_used_hides_what_a_called_macro_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _macros(inner_selector="#fail"))
    result = await execution.run_sequence(session=_session(), names=["outer"], args_list=[{"nat": NAT}])
    (step,) = result["steps"]
    assert step["ok"] is False
    assert step["args_used"] == {"nat": "<redacted>"}
    assert _absent(NAT, json.dumps(result))


@pytest.mark.asyncio
async def test_the_pill_text_never_carries_a_classified_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _macros())
    pushed = AsyncMock()
    monkeypatch.setattr(execution, "_push_status", pushed)
    await execution.run_macro(_session(), "outer", {"nat": NAT})
    texts = [str(call.kwargs.get("text")) for call in pushed.await_args_list]
    assert any("expect_text" in text for text in texts), texts
    assert all(_absent(NAT, text) for text in texts), texts


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["#row-{{id}}", "#fail"])
async def test_the_artifact_manifest_hides_what_a_called_macro_classified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, selector: str
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    for name, macro in _macros(selector).items():
        storage.write_macro(name=name, macro=macro)
    _install(monkeypatch, {})
    monkeypatch.setattr(execution, "load_macro", storage.load_macro)

    await macro_artifacts.run_macro_artifact(_FakeSession(tmp_path), "outer", {"nat": NAT}, capture=False, verify=False)

    manifest = json.loads((tmp_path / "recordings" / "artifacts" / "macros" / "outer" / "artifact.json").read_text())
    assert manifest["parameters"] == {"nat": "<redacted>"}
    on_disk = "\n".join(p.read_text(errors="replace") for p in (tmp_path / "recordings").rglob("*") if p.is_file())
    assert _absent(NAT, on_disk)
