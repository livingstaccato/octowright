# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exact shape of a ``screenshot_suppressed`` evidence record.

The other three record kinds are asserted whole in ``tests/test_artifacts_evidence.py``;
this one was not, so its ``id``/``type``/``label`` keys could be renamed freely.
The record exists so a screenshot's absence is visible: ``reports._render_summary``
prints its ``id``, ``type`` and ``label``, and a verification ``screenshot_exists``
check must NOT match it (its ``type`` is not ``screenshot``).
"""

from __future__ import annotations

import re
from pathlib import Path

from octowright.artifacts.evidence import EvidenceBuilder

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def test_a_screenshot_suppressed_record_has_exactly_the_documented_keys() -> None:
    builder = EvidenceBuilder()

    record = builder.screenshot_suppressed(label="after checkout")

    assert _ISO_Z.match(str(record["ts"])), record["ts"]
    assert {k: v for k, v in record.items() if k != "ts"} == {
        "id": "ev_001",
        "type": "screenshot_suppressed",
        "label": "after checkout",
    }
    assert builder.records == [record]


def test_a_suppressed_screenshot_takes_its_place_in_the_shared_id_sequence() -> None:
    builder = EvidenceBuilder()

    builder.screenshot(path=Path("/tmp/a.png"), label="a")
    record = builder.screenshot_suppressed(label="b")

    assert record["id"] == "ev_002"
