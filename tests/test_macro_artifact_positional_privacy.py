# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Artifact runs and CLI export classify arguments the way macro_run does.

An argument substituted into an ``expect_no_text``'s text IS the forbidden
text, so it is credential-tier whatever it is named
(``privacy.MacroArgPrivacy.for_macro``). ``macro_run`` knew that; the artifact
layer classified by name alone, so ``card`` -- not a credential name -- was
written cleartext to result.json and artifact.json ``parameters``, returned in
``plan_macro_artifact``'s ``args_used``, left the automatic screenshot
unredacted, and became the exported CLI's argparse default (CR3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._macro_artifact_fixtures import _CapturingSession, _reload, restore_reloaded_defaults

CARD = "4111111111111111"
ARGS = {"card": CARD}


@pytest.fixture(autouse=True)
def _restore() -> Any:
    yield
    restore_reloaded_defaults()


def _write(storage: Any) -> None:
    storage.write_macro(
        name="checkout",
        macro={
            "name": "checkout",
            "parameters": ["card"],
            "actions": [
                {"action": "navigate", "url": "https://shop.example.test/done"},
                {"action": "expect_no_text", "text": "{{card}}"},
            ],
        },
    )


def _tree_bytes(root: Path) -> bytes:
    return b"\n".join(path.read_bytes() for path in root.rglob("*") if path.is_file() and path.suffix != ".png")


def test_plan_redacts_an_assertion_arg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write(storage)
    result = macro_artifacts.plan_macro_artifact("checkout", dict(ARGS))
    assert result["args_used"]["card"] != CARD
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["parameters"]["card"] != CARD
    assert CARD.encode() not in _tree_bytes(tmp_path / "recordings")


@pytest.mark.asyncio
async def test_a_run_never_persists_an_assertion_arg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write(storage)
    screenshot_values: list[tuple[str, ...]] = []
    real_capture = macro_artifacts._capture_screenshot

    async def spy(**kwargs: Any) -> None:
        screenshot_values.append(tuple(kwargs.get("sensitive_values", ())))
        await real_capture(**kwargs)

    async def fake_run_macro(
        *, session: Any, name: str, args: Any, slowmo_ms: Any = None, **_private: Any
    ) -> dict[str, Any]:
        return {"macro": name, "executed": 2, "skipped": 0, "args_used": args, "slowmo_ms": 0}

    monkeypatch.setattr(macro_artifacts, "_capture_screenshot", spy)
    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", fake_run_macro)
    session = _CapturingSession(tmp_path)
    result = await macro_artifacts.run_macro_artifact(session, "checkout", dict(ARGS), capture=True, verify=False)

    run_result = json.loads(Path(result["paths"]["result"]).read_text(encoding="utf-8"))
    assert run_result["args_used"]["card"] != CARD
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["parameters"]["card"] != CARD
    assert CARD.encode() not in _tree_bytes(tmp_path / "recordings")
    # Both automatic screenshots go through octowright's redaction (or are
    # suppressed), which needs the value: an empty list meant "nothing to hide".
    assert screenshot_values == [(CARD,), (CARD,)]
    assert session.shots == []


def test_the_exported_cli_does_not_default_an_assertion_arg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write(storage)
    result = macro_artifacts.export_macro_cli(name="checkout", args=dict(ARGS))
    script = Path(result["path"]).read_text(encoding="utf-8")
    assert CARD not in script
    assert "default=''" in script
    manifest = json.loads((Path(result["path"]).parents[1] / "artifact.json").read_text(encoding="utf-8"))
    assert manifest["parameters"]["card"] != CARD


def test_the_compact_listing_redacts_an_assertion_arg_left_by_an_older_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A manifest written before this fix may hold the value; listing must not return it."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write(storage)
    plan = macro_artifacts.plan_macro_artifact("checkout", dict(ARGS))
    manifest_path = Path(plan["paths"]["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["parameters"] = dict(ARGS)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    listed = macro_artifacts.list_macro_artifacts("checkout")
    assert CARD not in json.dumps(listed)
