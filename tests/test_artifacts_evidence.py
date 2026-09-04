# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exact shape of every evidence record, and the scrubber that fills one in.

``EvidenceBuilder`` emits three record kinds and nothing asserted their key
SPELLING -- mutation testing left 23 of the module's 29 mutants alive, almost
all of them a dict key rewritten to something no reader looks up. That is not
cosmetic: ``reports._render_summary`` reads ``id``/``type``/``label``/
``description``, ``_redact_evidence`` dispatches on ``type`` and rewrites
``preview``, and ``script_export`` walks the same records. A renamed key
doesn't raise anywhere -- ``.get()`` returns ``None`` and the report renders a
bullet with a blank label, or the redactor stops recognising a log excerpt and
copies its preview through unscrubbed.

So each kind is asserted as a WHOLE dict rather than field by field: an exact
comparison is what catches a renamed key, and it also catches a field silently
added to one record kind and not the others.
"""

from __future__ import annotations

import re
from pathlib import Path

from octowright.artifacts.evidence import EvidenceBuilder, redact_preview

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def _without_ts(record: dict[str, object]) -> dict[str, object]:
    """Drop the timestamp after checking its shape -- it is the one unstable field."""
    assert _ISO_Z.match(str(record["ts"])), record["ts"]
    return {k: v for k, v in record.items() if k != "ts"}


def test_a_screenshot_record_has_exactly_the_documented_keys() -> None:
    builder = EvidenceBuilder()

    path = Path("/tmp/shot.png")

    record = builder.screenshot(path=path, label="after checkout")

    assert _without_ts(record) == {
        "id": "ev_001",
        "type": "screenshot",
        "path": str(path),
        "label": "after checkout",
    }


def test_an_artifact_record_has_exactly_the_documented_keys() -> None:
    builder = EvidenceBuilder()

    path = Path("/tmp/out.py")

    record = builder.artifact(path=path, kind="script", description="exported CLI")

    assert _without_ts(record) == {
        "id": "ev_001",
        "type": "artifact",
        "path": str(path),
        "kind": "script",
        "description": "exported CLI",
    }


def test_a_log_excerpt_record_has_exactly_the_documented_keys() -> None:
    """``length`` is measured on the RAW preview, before redaction.

    Redaction changes the string's length, so computing it after would report
    the scrubbed size and misdescribe how much of the log the excerpt covers.
    """
    builder = EvidenceBuilder()

    path = Path("/tmp/run.log")

    record = builder.log_excerpt(path=path, offset=128, preview="plain text")

    assert _without_ts(record) == {
        "id": "ev_001",
        "type": "log_excerpt",
        "path": str(path),
        "offset": 128,
        "length": 10,
        "preview": "plain text",
    }


def test_ids_are_sequential_and_zero_padded_across_record_kinds() -> None:
    """One counter serves all three builders, so the sequence must not restart per kind."""
    builder = EvidenceBuilder()

    ids = [
        builder.screenshot(path=Path("/tmp/a.png"), label="a")["id"],
        builder.artifact(path=Path("/tmp/b.json"), kind="json", description="b")["id"],
        builder.log_excerpt(path=Path("/tmp/c.log"), offset=0, preview="c")["id"],
    ]

    assert ids == ["ev_001", "ev_002", "ev_003"]
    assert [r["id"] for r in builder.records] == ids


def test_a_log_excerpt_preview_is_scrubbed_on_the_way_in() -> None:
    """The record on disk must never hold the secret, not merely the rendered report.

    ``reports._redact_evidence`` scrubs again at render time, which makes this
    look redundant -- it is not. That second pass only protects ``summary.md``;
    ``evidence.json`` is written from these records, and a caller that builds
    one and persists it by any other route gets whatever the builder stored.
    """
    builder = EvidenceBuilder()

    preview = "authorization: Bearer not-a-real-token"  # pragma: allowlist secret

    record = builder.log_excerpt(path=Path("/tmp/run.log"), offset=0, preview=preview)

    assert "not-a-real-token" not in str(record["preview"])
    assert "<redacted>" in str(record["preview"])


def test_the_length_field_measures_the_raw_preview_not_the_redacted_one() -> None:
    """Pins the ordering the record shape above only implies for a clean preview."""
    raw = "authorization: Bearer not-a-real-token"  # pragma: allowlist secret

    record = EvidenceBuilder().log_excerpt(path=Path("/tmp/run.log"), offset=0, preview=raw)

    assert record["length"] == len(raw)
    assert record["length"] != len(str(record["preview"]))


def test_redact_preview_leaves_an_ordinary_line_untouched() -> None:
    """The scrubber runs on every log excerpt, so a false positive costs real diagnostics."""
    line = "GET /orders/42 -> 200 in 31ms"

    assert redact_preview(line) == line
