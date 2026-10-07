# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The macro spans docs/telemetry.md documents: their names and attributes are a contract."""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros import execution
from octowright.macros.runtime import dispatch_simple
from tests.test_macro_credential_fill_origin import _session
from tests.test_telemetry_fixes import _setup_span_exporter

pytestmark = pytest.mark.anyio

LAUNCH = "https://app.example.test/"


class _ClickSession:
    instance_id = "inst-7"

    async def click(self, **kwargs: Any) -> None:
        pass


def _spans(exporter: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(span.name, dict(span.attributes or {})) for span in exporter.get_finished_spans()]


async def test_each_replayed_action_is_one_macro_action_span(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = _setup_span_exporter(monkeypatch)

    await dispatch_simple(
        _ClickSession(),  # type: ignore[arg-type]
        {"action": "click", "selector": "#go"},
        semantic_keys=("role", "name", "label", "text", "placeholder", "test_id"),
        strip_non_aria_noise=lambda kind, kwargs: kwargs,
        action_kwargs=lambda action: {k: v for k, v in action.items() if k != "action"},
    )

    assert _spans(exporter) == [("octowright.macro.action", {"action": "click", "instance_id": "inst-7"})]


async def test_a_macro_run_is_one_macro_run_span(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": []})
    exporter = _setup_span_exporter(monkeypatch)

    await execution.run_macro(session, "checkout", {})

    runs = [attrs for name, attrs in _spans(exporter) if name == "octowright.macro.run"]
    assert runs == [{"macro": "checkout", "instance_id": session.instance_id, "kind": session.kind}]
