# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Unit coverage for the aria credential scrubber's pure helpers."""

from __future__ import annotations

import pytest

from octowright.defaults import REDACTED_INPUT_PLACEHOLDER
from octowright.session import aria_redaction as ar


def test_password_value_is_blanked_from_the_tree() -> None:
    aria = "- document:\n  - textbox: tanuki-tim\n  - textbox: hunter2SECRET\n"
    out = ar.scrub_credentials(aria, ["hunter2SECRET"])
    assert "hunter2SECRET" not in out
    assert REDACTED_INPUT_PLACEHOLDER in out
    # Non-credential names survive: the tree stays useful to the agent.
    assert "tanuki-tim" in out


def test_newline_in_a_value_is_still_scrubbed() -> None:
    """Playwright normalizes an accessible name, so the raw value never matches.

    A password of "SECRET\\nLINE" renders as "SECRET LINE"; scrubbing only the
    raw form would leave it in the tree verbatim.
    """
    aria = "- document:\n  - textbox: SECRET LINE\n"
    out = ar.scrub_credentials(aria, ["SECRET\nLINE"])
    assert "SECRET LINE" not in out
    assert REDACTED_INPUT_PLACEHOLDER in out


def test_longest_value_is_scrubbed_first() -> None:
    """A short secret must not eat a longer one it is a substring of."""
    aria = "- textbox: abcdef\n"
    out = ar.scrub_credentials(aria, ["abc", "abcdef"])
    assert out.strip() == f"- textbox: {REDACTED_INPUT_PLACEHOLDER}"


def test_no_values_leaves_the_tree_untouched() -> None:
    aria = "- document:\n  - button: Confirm\n"
    assert ar.scrub_credentials(aria, []) == aria


@pytest.mark.parametrize(
    ("env", "expected"),
    [(None, "passwords"), ("off", "off"), ("ALL", "all"), ("  passwords ", "passwords")],
)
def test_mode_resolution(monkeypatch: pytest.MonkeyPatch, env: str | None, expected: str) -> None:
    if env is None:
        monkeypatch.delenv("OCTOWRIGHT_REDACT_INPUTS", raising=False)
    else:
        monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", env)
    assert ar.resolve_redaction_mode() == expected


def test_unknown_mode_falls_back_to_passwords_not_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must not silently disable redaction."""
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "pass-words")
    assert ar.resolve_redaction_mode() == "passwords"
