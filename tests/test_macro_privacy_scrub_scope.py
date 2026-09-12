# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The value-scrub set must carry data, not the field names that structure it.

``sensitive_arg_values`` feeds a blind substring scrub over every diagnostic
surface. A structural key name collected into that set ("name", "role", "id")
therefore rewrites unrelated failure text, which is exactly what the macro
failure bundle exists to preserve.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.macros.privacy import redact_args, scrub_sensitive_values, sensitive_arg_values

STRUCTURAL_ARGS = {"user": {"name": "a4-subject-canary", "role": "a4-role-canary"}}
DIAGNOSTIC = "timeout waiting for selector [name=q] role=listbox id=main"


def _exported_module(name: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    source = render_macro_cli(name=name, macro={"actions": []})
    module: dict[str, Any] = {"__name__": name.replace("-", "_")}
    exec(compile(source, f"<{name}>", "exec"), module)
    return module


def test_structural_key_names_below_a_classified_branch_are_not_scrub_tokens() -> None:
    values = sensitive_arg_values(STRUCTURAL_ARGS)

    assert "a4-subject-canary" in values
    assert "a4-role-canary" in values
    assert "name" not in values
    assert "role" not in values


def test_a_classified_branch_does_not_rewrite_unrelated_failure_text() -> None:
    values = sensitive_arg_values(STRUCTURAL_ARGS)

    assert scrub_sensitive_values(DIAGNOSTIC, values) == DIAGNOSTIC


def test_identity_shaped_keys_below_a_classified_branch_stay_scrubbed() -> None:
    args = {"credential": {"a4-keyed-canary@example.test": "A4-KEYED-VALUE-CANARY"}}

    values = sensitive_arg_values(args)

    assert "a4-keyed-canary@example.test" in values
    assert "A4-KEYED-VALUE-CANARY" in values


def test_exported_classifier_matches_runtime_on_structural_key_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _exported_module("privacy-scrub-scope", monkeypatch)

    exported = tuple(module["_sensitive_arg_values"](STRUCTURAL_ARGS))

    assert exported == sensitive_arg_values(STRUCTURAL_ARGS)
    assert module["_redact_value"](DIAGNOSTIC, list(exported)) == DIAGNOSTIC


# A classified key holding a SEQUENCE was reachable code with no test behind it
# -- found by mutation testing, not by coverage of the feature's own contract
# tests. Six mutants of the sequence arm survived (including replacing the
# recursive call's whole result with None), which is the same thing as saying
# the arm could be deleted and the suite would stay green. The behaviour is
# correct today; these pin it, because a macro argument holding a list of
# credentials leaks every one of them if this arm regresses.
@pytest.mark.parametrize(
    "container",
    [
        pytest.param(list, id="list"),
        pytest.param(tuple, id="tuple"),
        pytest.param(set, id="set"),
        pytest.param(frozenset, id="frozenset"),
    ],
)
def test_every_value_in_a_classified_sequence_is_collected(container: type) -> None:
    args = {"password": container(["A4-SEQ-FIRST-CANARY", "A4-SEQ-SECOND-CANARY"])}

    values = sensitive_arg_values(args)

    assert set(values) == {"A4-SEQ-FIRST-CANARY", "A4-SEQ-SECOND-CANARY"}


def test_a_classified_sequence_nested_under_an_ordinary_container_is_collected() -> None:
    """The sequence arm must keep the inherited classification as it recurses."""
    args = {"payload": {"profile": {"token": ["A4-NESTED-SEQ-CANARY"]}}, "display": "public"}

    values = sensitive_arg_values(args)

    assert values == ("A4-NESTED-SEQ-CANARY",)
    assert "public" not in values


def test_an_unclassified_sequence_is_not_collected() -> None:
    """Recursing into a sequence must not invent classification it did not inherit."""
    assert sensitive_arg_values({"display": ["a4-plain-one", "a4-plain-two"]}) == ()


def test_empty_and_missing_values_are_not_collected() -> None:
    """An empty value would scrub every empty substring -- i.e. the whole text."""
    assert sensitive_arg_values({"password": "", "token": None, "secret": [""]}) == ()


# The scrub's word-boundary threshold: below it a value matches only on an
# alphanumeric boundary, at or above it anywhere. Nothing exercised either side
# of that comparison, so `<` and `<=` were interchangeable -- and the two differ
# for exactly the 4-character secret.
def test_a_secret_below_the_threshold_matches_only_on_a_word_boundary() -> None:
    """Three characters is short enough to occur inside unrelated words."""
    scrubbed = scrub_sensitive_values("abc abcde", ("abc",))

    assert scrubbed == "<redacted> abcde"


def test_a_secret_at_the_threshold_matches_inside_a_longer_word() -> None:
    """Four characters is the first length caught mid-word, so `<` is not `<=`."""
    scrubbed = scrub_sensitive_values("abcd abcde", ("abcd",))

    assert scrubbed == "<redacted> <redacted>e"


def test_longer_values_are_scrubbed_before_shorter_ones_they_contain() -> None:
    """Ordering is load-bearing: a short secret must not eat a longer one.

    ``sensitive_arg_values`` sorts longest-first for this reason, and
    ``_serialized_variants`` does the same within one value. Dropping either
    ``key=len`` or ``reverse=True`` survived every test in the suite -- the
    scrub still redacted *something*, so an assertion on "the secret is gone"
    cannot see the difference. This asserts on the surviving text instead.
    """
    args = {"password": "a4secret", "pin": "a4s"}  # pragma: allowlist secret

    scrubbed = scrub_sensitive_values("value a4secret here", sensitive_arg_values(args))

    # Short-first would match "a4s" inside "a4secret" and stop there, leaving
    # the tail of the longer credential sitting in the diagnostic.
    assert scrubbed == "value <redacted> here"


def test_a_non_ascii_secret_is_scrubbed_in_its_escaped_spelling() -> None:
    """Both ``ensure_ascii`` spellings are generated, so both must be caught."""
    secret = "a4-café-canary"  # pragma: allowlist secret

    assert scrub_sensitive_values(secret, (secret,)) == "<redacted>"
    # The \\u00e9 form is what a JSON-serialized diagnostic carries.
    escaped = secret.replace("é", "\\u00e9")
    assert scrub_sensitive_values(f"body {escaped} tail", (secret,)) == "body <redacted> tail"


@pytest.mark.parametrize("container", [list, tuple])
def test_the_scrub_walks_sequences_of_every_shape(container: type) -> None:
    """Both recursive arms carry the args forward; neither had a test."""
    scrubbed = scrub_sensitive_values(container(["hold A4-SEQ-CANARY", "clean"]), ("A4-SEQ-CANARY",))

    assert scrubbed == container(["hold <redacted>", "clean"])


@pytest.mark.parametrize("container", [list, tuple])
def test_key_redaction_walks_sequences_of_every_shape(container: type) -> None:
    redacted = redact_args({"outer": container([{"password": "A4-DEEP-CANARY"}, {"note": "clean"}])})

    assert redacted == {"outer": container([{"password": "<redacted>"}, {"note": "clean"}])}


def test_the_redaction_marker_is_honoured_everywhere_it_is_threaded() -> None:
    """`marker` is forwarded through four recursive call sites and one scrub."""
    redacted = redact_args({"password": "A4-MARKER-CANARY", "note": "A4-MARKER-CANARY tail"}, marker="[gone]")

    assert redacted == {"password": "[gone]", "note": "[gone] tail"}
