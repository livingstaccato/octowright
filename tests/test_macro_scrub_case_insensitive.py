# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Every scrub ignores case, in every spelling it handles (#248).

The shared scrub matched case exactly (only percent-encoded spellings were
case-folded), so a page that echoed a credential upper-cased, lower-cased or
mixed -- a header that capitalises, a log that shouts -- put it in a live
failure payload (``original``, the console tail, failed requests) in the
clear. Artifact bundles had a tripwire for it; live payloads and the exported
script did not.
"""

from __future__ import annotations

import contextlib
import json
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.macros import execution, failure_context, privacy

SECRET = "Case-Pw\"q'x-9Rt"  # pragma: allowlist secret
CASES = {"upper": str.upper, "lower": str.lower, "mixed": str.swapcase}


def _spellings(value: str) -> dict[str, str]:
    return {
        "raw": value,
        "json": json.dumps(value)[1:-1],
        "url": quote(value, safe=""),
        "repr": repr(value + "'\"")[1:-4],
    }


def _all_cased() -> list[tuple[str, str]]:
    return [
        (f"{case}-{kind}", spelling)
        for case, fold in CASES.items()
        for kind, spelling in _spellings(fold(SECRET)).items()
    ]


def _leaks(text: str) -> list[str]:
    folded = text.casefold()
    return [name for name, spelling in _all_cased() if spelling.casefold() in folded]


@pytest.mark.parametrize(("name", "spelling"), _all_cased())
def test_the_shared_scrub_ignores_case(name: str, spelling: str) -> None:
    text = f"page said [{spelling}] and again {spelling}"
    scrubbed = privacy.scrub_sensitive_values(text, (SECRET,))
    assert scrubbed == "page said [<redacted>] and again <redacted>", name


@pytest.mark.parametrize("case", CASES)
def test_the_recorder_scrub_ignores_case(case: str) -> None:
    ledger = privacy.PrivacyLedger([SECRET])
    assert ledger.scrub({"text": f"x {CASES[case](SECRET)} y"}) == {"text": "x <redacted> y"}


def test_a_typed_password_still_matches_only_as_a_whole_identifier() -> None:
    ledger = privacy.PrivacyLedger()
    ledger.add(["admin"], word_bounded=True)
    assert ledger.scrub("pw=ADMIN, menu #Admin-menu, Administrator") == "pw=<redacted>, menu #Admin-menu, Administrator"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES)
async def test_a_live_failure_payload_holds_no_cased_echo(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    echo = CASES[case](SECRET)
    session = MagicMock()
    session.instance_id = "case-1"
    session.kind = "chromium"
    session.durable_text_scrubber = None
    session.recorder = MagicMock()
    session.diagnostic_bundle = AsyncMock(return_value={"console_tail": [{"text": f"typed {echo}"}]})
    monkeypatch.setattr(execution, "load_macro", lambda _n: {"actions": [{"action": "click", "selector": "#a"}]})
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(execution, "credential_fill_guard", lambda *_a: contextlib.nullcontext())
    monkeypatch.setattr(failure_context, "failed_requests_tail", lambda _s: [{"url": "https://x.test/", "body": echo}])

    async def boom(*_a: Any, **_kw: Any) -> tuple[int, int]:
        raise RuntimeError(f"expected 'ok', got {echo!r}; page error: {echo}")

    monkeypatch.setattr(execution, "dispatch_plain_action", boom)

    with pytest.raises(RuntimeError) as caught:
        await execution.run_macro(session, "m", {"password": SECRET})

    payload = caught.value.args[0]
    assert "<redacted>" in payload["original"]
    assert _leaks(json.dumps(payload)) == []
    assert _leaks(str(caught.value)) == []


@pytest.mark.parametrize("case", CASES)
def test_the_exported_script_scrub_ignores_case(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    module: dict[str, Any] = {"__name__": "case_cli"}
    exec(compile(render_macro_cli(name="m", macro={"actions": []}), "<case-cli>", "exec"), module)

    for kind, spelling in _spellings(CASES[case](SECRET)).items():
        assert module["_redact_value"](f"got {spelling}!", [SECRET]) == "got <redacted>!", kind


def test_non_ascii_text_falls_back_to_unicode_case_folding() -> None:
    """The Kelvin sign (U+212A) folds to ``k``: lower-casing cannot see that, the regex can."""
    ledger = privacy.PrivacyLedger(["kelvin-Pw-77"])
    assert ledger.scrub("echo \u212aELVIN-pw-77 é") == "echo <redacted> é"


def test_a_non_ascii_variant_is_still_checked_against_ascii_text() -> None:
    """A long s (U+017F) folds to ``s``: a value spelled with one matches plain ASCII text."""
    ledger = privacy.PrivacyLedger(["pa\u017f\u017f-Word-9"])
    assert ledger.scrub("typed PASS-word-9") == "typed <redacted>"
