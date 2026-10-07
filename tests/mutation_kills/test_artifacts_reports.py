# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Whole-file contents of a run bundle and an artifact manifest.

``tests/test_artifacts_reports.py`` checks individual fields; these compare the
files a run leaves on disk in full, because each is read by something other
than the code that wrote it: ``result.json`` and ``evidence.json`` by
verification, ``verification.json`` by ``macro_artifact_status``, and
``summary.md`` by a person. A whole-file comparison is what catches a renamed
key (which ``.get()`` readers turn into a silent ``None``), a dropped fallback
in the rendered report, and a verification that is accepted but never written
or never scrubbed.
"""

from __future__ import annotations

import json
import locale
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from octowright.artifacts.evidence import EvidenceBuilder
from octowright.artifacts.reports import refresh_run_summary, write_artifact_manifest, write_run_bundle

_SECRET = "zebrin4-not-real"  # pragma: allowlist secret


def _result() -> dict[str, Any]:
    """A raw result, args NOT yet redacted, so the bundle's own redaction is what is tested."""
    return {
        "run_id": "run_0001",
        "status": "failed",
        "instance_id": "inst-1",
        "macro": "checkout",
        "args_used": {"password": _SECRET, "sku": "42"},
        "executed": 3,
        "skipped": 1,
        "error": None,
        "recording_path": None,
        "evidence_path": None,
    }


def _verification() -> dict[str, Any]:
    return {
        "status": "failed",
        "critical_points": [
            {
                "id": "cp1",
                "description": "Order placed",
                "status": "failed",
                "checks": [{"type": "log_contains", "status": "failed", "message": f"{_SECRET} not in log"}],
            },
            {},
            {"id": "cp3", "description": "Fallbacks", "status": "passed", "checks": [{}]},
        ],
    }


def test_a_run_bundle_with_verification_writes_exactly_these_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "artifacts" / "checkout" / "run_0001"
    builder = EvidenceBuilder()
    builder.screenshot(path=tmp_path / "after.png", label="after")
    builder.log_excerpt(path=tmp_path / "run.log", offset=0, preview="ok")

    paths = write_run_bundle(
        run_dir=run_dir,
        result=_result(),
        evidence=builder.records,
        summary="Checkout ran.",
        verification=_verification(),
        sensitive_values=(_SECRET,),
    )

    assert paths == {
        "result": run_dir / "result.json",
        "evidence": run_dir / "evidence.json",
        "verification": run_dir / "verification.json",
        "summary": run_dir / "summary.md",
    }
    assert json.loads((run_dir / "result.json").read_text(encoding="utf-8")) == {
        **_result(),
        "args_used": {"password": "<redacted>", "sku": "42"},
        "evidence_path": str(run_dir / "evidence.json"),
        "summary": "Checkout ran.",
    }
    assert json.loads((run_dir / "evidence.json").read_text(encoding="utf-8")) == {"records": builder.records}

    scrubbed = _verification()
    scrubbed["critical_points"][0]["checks"][0]["message"] = "<redacted> not in log"
    assert json.loads((run_dir / "verification.json").read_text(encoding="utf-8")) == scrubbed

    assert (run_dir / "summary.md").read_text(encoding="utf-8") == "\n".join(
        [
            "# Macro Artifact Run: checkout",
            "",
            "Checkout ran.",
            "",
            "## Result",
            "",
            "- Status: `failed`",
            "- Run ID: `run_0001`",
            "- Executed: `3`",
            "- Skipped: `1`",
            "",
            "## Evidence",
            "",
            "- `ev_001` `screenshot`: after",
            "- `ev_002` `log_excerpt`: log_excerpt",
            "",
            "## Verification and Critical Points",
            "",
            "**Verification Status**: `failed`",
            "",
            "### cp1: Order placed",
            "- Status: `failed`",
            "- Checks:",
            "  - `log_contains`: `failed` - <redacted> not in log",
            "",
            "### CP: Unknown",
            "- Status: `unknown`",
            "",
            "### cp3: Fallbacks",
            "- Status: `passed`",
            "- Checks:",
            "  - `unknown`: `unknown` - ",
            "",
            "",
        ]
    )


def test_json_files_are_written_with_two_space_indentation(tmp_path: Path) -> None:
    """The bundle is meant to be read by a person too; indentation is part of that."""
    paths = write_run_bundle(run_dir=tmp_path, result={"run_id": "r"}, evidence=[], summary="s")

    text = paths["evidence"].read_text(encoding="utf-8")

    assert text == '{\n  "records": []\n}'


def test_a_refreshed_summary_of_an_empty_run_spells_out_every_fallback(tmp_path: Path) -> None:
    """A bundle missing every field still renders, and says so rather than going blank."""
    path = refresh_run_summary(run_dir=tmp_path, result={}, evidence=[], verification={})

    assert path == tmp_path / "summary.md"
    assert path.read_text(encoding="utf-8") == "\n".join(
        [
            "# Macro Artifact Run: ",
            "",
            "Ran macro unknown: status=unknown, executed=0, skipped=0.",
            "",
            "## Result",
            "",
            "- Status: `None`",
            "- Run ID: `None`",
            "- Executed: `None`",
            "- Skipped: `None`",
            "",
            "## Evidence",
            "",
            "No evidence records captured.",
            "",
            "## Verification and Critical Points",
            "",
            "**Verification Status**: `unknown`",
            "",
            "No critical points evaluated.",
            "",
        ]
    )


def test_a_written_manifest_is_the_input_with_redacted_parameters_and_a_new_stamp(tmp_path: Path) -> None:
    manifest = {
        "name": "checkout",
        "parameters": {"password": _SECRET, "sku": "42"},
        "updated_at": "2000-01-01T00:00:00Z",
    }
    target = tmp_path / "a" / "b" / "artifact.json"

    path = write_artifact_manifest(target, manifest)

    data = json.loads(target.read_text(encoding="utf-8"))
    assert path == target
    assert data["updated_at"] != "2000-01-01T00:00:00Z"
    assert data == {
        "name": "checkout",
        "parameters": {"password": "<redacted>", "sku": "42"},
        "updated_at": data["updated_at"],
    }
    assert target.read_text(encoding="utf-8") == json.dumps(data, indent=2, ensure_ascii=False)


@pytest.fixture
def ascii_locale() -> Iterator[None]:
    """Make the process's locale encoding ASCII, as on a host with a non-UTF-8 locale.

    ``open(..., encoding=None)`` reads the C library's codeset, so switching
    ``LC_CTYPE`` to ``C`` is what a non-UTF-8 host looks like to it. UTF-8 mode
    overrides the locale entirely, so there the test can say nothing.
    """
    if sys.flags.utf8_mode:
        pytest.skip("UTF-8 mode ignores the locale encoding")
    previous = locale.setlocale(locale.LC_CTYPE)
    locale.setlocale(locale.LC_CTYPE, "C")
    try:
        if locale.getencoding().lower().replace("-", "") == "utf8":
            pytest.skip("this platform's C locale is UTF-8")
        yield
    finally:
        locale.setlocale(locale.LC_CTYPE, previous)


@pytest.mark.usefixtures("ascii_locale")
def test_a_refreshed_summary_is_utf8_whatever_the_locale(tmp_path: Path) -> None:
    """``summary.md`` is UTF-8 by contract, not by the host's locale.

    A non-ASCII macro name must be written, and as UTF-8, even where the
    locale encoding could not represent it.
    """
    refresh_run_summary(run_dir=tmp_path, result={"macro": "café"}, evidence=[], verification={})

    # Text mode writes the platform's newline (CRLF on Windows); only the encoding is pinned here.
    written = (tmp_path / "summary.md").read_bytes().replace(b"\r\n", b"\n")
    assert written.startswith("# Macro Artifact Run: café\n".encode())
