# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The allowed-field derivation must agree with what replay actually does.

``lint_fields`` claims its allowed set is derived from the real dispatch
pipeline. A hand-written table drifts (``RECORDER_NOISE`` already did, for 608
bogus errors), but a *derivation that models the pipeline incompletely* drifts
the same way while looking principled. These tests pin both directions against
the real ``runtime.dispatch_simple``:

* nothing lint ALLOWS may blow up in dispatch (a whitelist that green-lights a
  ``TypeError`` inverts the check's whole purpose);
* nothing the recorder legitimately WRITES may be flagged (an error-severity
  false positive hard-blocks the dashboard macro editor, which refuses to save
  while ``error_count`` is non-zero).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from octowright import defaults
from octowright.macros.lint import lint_macro
from octowright.macros.lint_fields import allowed_fields_for
from octowright.macros.runtime import _ACTION_MAP, dispatch_simple
from octowright.macros.substitution import (
    SEMANTIC_LOCATOR_KEYS,
    action_kwargs,
    strip_non_aria_noise,
)
from octowright.session import BrowserSession

# Fields whose VALUE the dispatcher inspects before any signature is consulted,
# so a probe can't pass a bare sentinel. Only the binding matters here, never
# the behaviour. `path` is handled separately below because `screenshot` runs it
# through reject_unsafe_path against a root other tests monkeypatch.
_PROBE_VALUES: dict[str, Any] = {
    "timeout_ms": 1000,
    "page_index": 0,
    "index": 0,
    "width": 800,
    "height": 600,
}


class _SignatureCheckingSession:
    """A session that accepts a call only if the real method signature would.

    Binding against ``BrowserSession``'s own signature reproduces the exact
    ``TypeError`` a live replay would raise, with no browser and no mock that
    quietly swallows an unexpected keyword.
    """

    def __init__(self) -> None:
        self.instance_id = "probe"
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        real = getattr(BrowserSession, name)  # AttributeError propagates

        async def _call(*args: Any, **kwargs: Any) -> dict[str, Any]:
            # bind_partial, not bind: this probe tests which KEYWORDS the
            # method accepts, not whether every required argument is present.
            inspect.signature(real).bind_partial(self, *args, **kwargs)
            self.calls.append((name, kwargs))
            return {"ok": True}

        return _call


async def _dispatch(action: dict[str, Any]) -> _SignatureCheckingSession:
    session = _SignatureCheckingSession()
    await dispatch_simple(
        session,  # type: ignore[arg-type]
        action,
        semantic_keys=SEMANTIC_LOCATOR_KEYS,
        strip_non_aria_noise=strip_non_aria_noise,
        action_kwargs=action_kwargs,
    )
    return session


@pytest.mark.parametrize("kind", sorted(_ACTION_MAP))
@pytest.mark.asyncio
async def test_every_allowed_field_survives_real_dispatch(kind: str) -> None:
    """Whatever lint allows, dispatch must accept — one probe per field.

    Fails loudly for `comment`/`description`/`name`/`optional`: nothing in the
    replay pipeline strips them, so they reach the session method as unexpected
    keywords. A lint that blesses them green-lights the exact failure it exists
    to catch.
    """
    for field in sorted(allowed_fields_for(kind)):
        if field == "action":
            continue  # the discriminator itself, never forwarded as a kwarg
        # RECORDINGS_DIR is read per-call: another test monkeypatches it, and a
        # value captured at import time would resolve outside the patched root.
        default = str(defaults.RECORDINGS_DIR / "probe.png") if field == "path" else "v"
        action = {"action": kind, field: _PROBE_VALUES.get(field, default)}
        try:
            await _dispatch(action)
        except TypeError as exc:  # pragma: no cover - the assertion is the report
            pytest.fail(f"lint allows {field!r} on {kind!r} but dispatch raised: {exc}")


@pytest.mark.parametrize(
    ("action", "why"),
    [
        (
            {"action": "click", "ts": 1.0, "selector": "#save", "role": "button", "role_name": "Save"},
            "BrowserSession.click records **_resolve_semantic_metadata() on every click",
        ),
        (
            {"action": "fill", "ts": 1.0, "selector": "#u", "value": "v", "role": "textbox", "role_name": "User"},
            "BrowserSession.fill stamps the same metadata",
        ),
        (
            {
                "action": "type",
                "ts": 1.0,
                "selector": "#q",
                "text": "hi",
                "delay_ms": None,
                "role": "textbox",
                "role_name": "Search",
            },
            "type is stripped by strip_non_aria_noise before dispatch",
        ),
        (
            {"action": "navigate", "ts": 1.0, "url": "https://example.com", "persona": "tanuki-tim"},
            "the scenario layer stamps persona onto every merged event",
        ),
        (
            {"action": "wait_for", "ts": 1.0, "expression": "1", "role": "player", "persona": "p"},
            "scenario-stamped role/persona on a non-semantic action",
        ),
    ],
)
def test_recorder_written_shapes_lint_clean(action: dict[str, Any], why: str) -> None:
    """A shape octowright's own recorder writes must never be an error."""
    issues = [i for i in lint_macro({"name": "t", "actions": [action]}) if i.code == "unknown_field"]
    assert issues == [], f"{why}; got {[i.message for i in issues]}"


@pytest.mark.parametrize(
    ("action", "field"),
    [
        ({"action": "navigate", "url": "https://e.com", "comment": "why"}, "comment"),
        ({"action": "navigate", "url": "https://e.com", "description": "d"}, "description"),
        ({"action": "navigate", "url": "https://e.com", "optional": True}, "optional"),
        ({"action": "click_by", "role": "button", "name": "Submit"}, "name"),
    ],
)
def test_fields_that_break_replay_are_flagged(action: dict[str, Any], field: str) -> None:
    """`name` is the worst of these: it is Playwright's own spelling of
    `role_name`, and filtering it out silently clicks the FIRST button."""
    codes = [i.code for i in lint_macro({"name": "t", "actions": [action]})]
    assert "unknown_field" in codes, f"{field!r} reaches the session method and raises TypeError"


@pytest.mark.parametrize("kind", sorted(_ACTION_MAP))
def test_unknown_field_check_is_reachable_for_every_dispatchable_kind(kind: str) -> None:
    """The check ran only for `_SIMPLE_REQUIRED`, a strict subset of `_ACTION_MAP`.

    get_text_by is the sharp edge: it takes **finders, so a stray key is passed
    to the locator builder as though it were a finder rather than rejected.
    """
    action = {"action": kind, "octowright_definitely_not_a_field": "x"}
    codes = [i.code for i in lint_macro({"name": "t", "actions": [action]})]
    assert "unknown_field" in codes


def test_screenshot_message_does_not_claim_a_typeerror_it_cannot_raise() -> None:
    """`screenshot` is the one kind `_dispatch_standard` special-cases.

    It forwards only `path` and ignores the rest of the action, so a stray
    field there is silently DROPPED rather than raising. Telling the author to
    expect a TypeError sends them looking for a crash that never comes.
    """
    issues = [
        i
        for i in lint_macro({"name": "t", "actions": [{"action": "screenshot", "path": "p.png", "full_page": True}]})
        if i.code == "unknown_field"
    ]
    assert len(issues) == 1
    assert "TypeError" not in issues[0].message
    assert "ignores" in issues[0].message


def test_mixed_type_action_keys_do_not_crash_the_linter() -> None:
    """A YAML macro can carry non-string keys, and lint must stay total.

    ``dsl.py`` loads macros with ``yaml.safe_load``; YAML 1.1 resolves a bare
    ``on:``/``yes:``/``no:`` key to a bool, and unknown keys are passed through
    verbatim. Sorting that key set raises ``TypeError: '<' not supported
    between instances of 'str' and 'bool'`` — an analyzer that crashes on its
    input rather than reporting on it, while every other malformed-input path
    in ``lint.py`` is explicitly guarded.
    """
    macro = {"name": "t", "actions": [{"action": "navigate", "url": "https://x.com", True: 1, "js": 2}]}
    codes = [i.code for i in lint_macro(macro)]
    assert "unknown_field" in codes


@pytest.mark.parametrize(
    ("action", "pair"),
    [
        ({"action": "mock_route", "pattern": "**/api/v1/**", "url_pattern": "**/api/**"}, "pattern/url_pattern"),
        ({"action": "drag", "source": "#a", "source_selector": "#b", "target": "#t"}, "source/source_selector"),
    ],
)
def test_carrying_both_rename_spellings_is_an_error_not_a_coin_flip(action: dict[str, Any], pair: str) -> None:
    """`_REPLAY_RENAME_KEYS` makes the recorded spelling legal alongside the
    method's own parameter name, with no mutual-exclusion check anywhere.

    `_normalize_replay_kwargs` then picks a winner by dict insertion order, so
    a formatter, a diff merge, or a round-trip through a tool that rebuilds the
    dict silently changes which pattern is installed -- with no error at save
    time or replay time. Lint is where both spellings are already enumerated.
    """
    issues = [i for i in lint_macro({"name": "t", "actions": [action]}) if i.code == "ambiguous_field"]
    assert len(issues) == 1, f"{pair} must not be a silent coin flip"


def test_either_rename_spelling_alone_is_fine() -> None:
    for action in ({"action": "mock_route", "pattern": "**/a/**"}, {"action": "mock_route", "url_pattern": "**/a/**"}):
        assert [i for i in lint_macro({"name": "t", "actions": [action]}) if i.code == "ambiguous_field"] == []
