# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_compile(write=True)`` keeps what the version on disk declared (#248).

A compiled YAML document has no ``created_at`` and, unless the YAML declares
them, no ``parameter_specs``. Writing it as-is reset the creation time and
silently dropped the declared sensitivity, so a parameter its author had made a
credential was shown in run results again with nothing saying so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright.macros import dsl
from tests._macro_artifact_fixtures import _reload, restore_reloaded_defaults

SPECS = {"display": {"sensitive": True}}
YAML = "name: checkout\nparameters: [display]\nactions:\n  - {action: expect_text, selector: body, text: '{{display}}'}\n"


@pytest.fixture(autouse=True)
def _restore() -> Any:
    yield
    restore_reloaded_defaults()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    module, _artifacts = _reload(monkeypatch, tmp_path)
    return module


def _on_disk(storage: Any) -> dict[str, Any]:
    return json.loads(storage.macro_path("checkout").read_text(encoding="utf-8"))


def _seed(storage: Any) -> dict[str, Any]:
    storage.write_macro(
        name="checkout",
        macro={**dsl.compile_macro_yaml(YAML), "parameter_specs": SPECS, "created_at": "2026-01-02T03:04:05Z"},
    )
    return _on_disk(storage)


def test_a_compiled_write_keeps_the_specs_and_creation_time(storage: Any) -> None:
    _seed(storage)
    _path, findings = storage.write_compiled_macro(name="checkout", macro=dsl.compile_macro_yaml(YAML))
    written = _on_disk(storage)
    assert written["parameter_specs"] == SPECS
    assert written["created_at"] == "2026-01-02T03:04:05Z"
    assert findings == []


def test_specs_the_yaml_declares_replace_the_saved_ones_and_a_shrink_is_reported(storage: Any) -> None:
    _seed(storage)
    yaml = YAML + "parameter_specs:\n  display: {sensitive: false}\n"
    compiled = dsl.compile_macro_yaml(yaml)
    assert compiled["parameter_specs"] == {"display": {"sensitive": False}}
    _path, findings = storage.write_compiled_macro(name="checkout", macro=compiled)
    assert _on_disk(storage)["parameter_specs"] == {"display": {"sensitive": False}}
    assert [code for code, _message in findings] == ["sensitive_parameters_shrank"]


def test_a_first_compiled_write_stamps_a_creation_time(storage: Any) -> None:
    storage.write_compiled_macro(name="checkout", macro=dsl.compile_macro_yaml(YAML))
    written = _on_disk(storage)
    assert written["created_at"]
    assert "parameter_specs" not in written


def test_a_document_without_specs_compiles_without_the_key() -> None:
    assert "parameter_specs" not in dsl.compile_macro_yaml(YAML)


def test_the_tool_writes_through_the_keeping_writer_and_returns_the_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server import macros as server_macros

    fake = MagicMock()
    fake.write_compiled_macro.return_value = (Path("/tmp/checkout.json"), [("sensitive_parameters_shrank", "shrank")])
    monkeypatch.setattr(server_macros, "macro_mod", fake)
    out = server_macros.macro_compile(YAML, write=True)
    assert out["written"] is True
    assert out["warnings"] == ["shrank"]
    fake.write_macro.assert_not_called()
