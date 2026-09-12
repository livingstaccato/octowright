# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The three key classifiers must agree on a union superset.

``macros.privacy.is_sensitive_arg_key``, ``artifacts.redaction.is_sensitive_key``
and ``macros.substitution.is_credential_arg`` each decided sensitivity
independently and disagreed on 1173 of 1916 sampled names, which is not a
difference of opinion but four live holes: ``private_key``, ``cookie`` and
``set_cookie`` were missed by the privacy classifier while the redaction one
caught them, ``otp`` was known only to the sink guard, and plural forms bypassed
all three.

The invariant is measured, not asserted. ``tests/fixtures/privacy_classifier_baseline.json``
freezes what every classifier decided at 0.23.0; no name sensitive to *any* of
them may become insensitive to the unified one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from octowright.artifacts.redaction import is_sensitive_key
from octowright.macros.privacy import is_sensitive_arg_key
from octowright.macros.substitution import is_credential_arg

_BASELINE_PATH = Path(__file__).parent / "fixtures" / "privacy_classifier_baseline.json"

# Missed by the privacy classifier while another classifier caught them. Each
# reached args_used, manifests and generated exports in cleartext.
CLOSED_HOLES = ("private_key", "cookie", "cookies", "set_cookie", "otp")

# Bypassed every classifier: the token set is singular and nothing stems.
PLURAL_FORMS = ("passwords", "tokens", "secrets", "api_keys", "access_keys", "credentials")

# Treated as credentials by at least one classifier, but NOT sink-blocked, so
# `{{passphrase}}` could expand into a URL or evaluate() while `{{password}}`
# was refused.
SINK_GAPS = ("passphrase", "pw", "pwd", "authorization", "access_key", "private_key")


@pytest.fixture(scope="module")
def baseline() -> dict[str, Any]:
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def test_no_name_sensitive_at_0_23_0_becomes_insensitive(baseline: dict[str, Any]) -> None:
    """The union invariant. Unifying vocabularies must never narrow one."""
    lost = [name for name, _flags in baseline["sensitive_to_any"] if not is_sensitive_arg_key(name)]

    assert not lost, f"{len(lost)} name(s) lost sensitivity, e.g. {sorted(lost)[:12]}"


def test_hazard_names_do_not_become_sensitive(baseline: dict[str, Any]) -> None:
    """Widening must not over-tighten: `browser` contains `user`, `author` contains `auth`."""
    gained = [name for name in baseline["insensitive"] if is_sensitive_arg_key(name)]

    assert not gained, f"over-tightened onto ordinary names: {gained}"


@pytest.mark.parametrize("key", CLOSED_HOLES)
def test_privacy_classifier_covers_every_redaction_token(key: str) -> None:
    assert is_sensitive_arg_key(key) is True


@pytest.mark.parametrize("key", PLURAL_FORMS)
def test_plural_key_forms_classify(key: str) -> None:
    assert is_sensitive_arg_key(key) is True


@pytest.mark.parametrize("key", SINK_GAPS)
def test_sink_guard_covers_the_whole_credential_tier(key: str) -> None:
    assert is_credential_arg(key) is True


def test_depluralization_does_not_break_the_access_key_pair() -> None:
    """`access` ends in `s` without being plural; a naive strip destroys the pair."""
    assert is_sensitive_arg_key("access_key") is True
    assert is_sensitive_arg_key("access_keys") is True


def test_redaction_classifier_stays_a_subset_of_the_unified_one(baseline: dict[str, Any]) -> None:
    """`artifacts.redaction` keeps its own callers (`macros/lint_urls.py`), so it
    must not drift below the union while it still exists."""
    lost = [name for name, flags in baseline["sensitive_to_any"] if "r" in flags and not is_sensitive_key(name)]

    assert not lost, f"redaction classifier narrowed: {sorted(lost)[:12]}"
