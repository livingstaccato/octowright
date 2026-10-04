# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The credential-header guard judges the pattern replay will actually install.

``inject_headers`` and ``mock_route`` accept the recorder's ``pattern`` and the
session's own ``url_pattern``. The guard read ``pattern`` first, while replay
renamed ``pattern`` to ``url_pattern`` in key order and let the later key win:
``{"pattern": "https://app.example.test/**", "url_pattern":
"https://evil.test/**", "headers": {"X-Leak": "{{password}}"}}`` passed the
own-site check on the first spelling and installed the second. Aliases are now
resolved once, before both, and two spellings that disagree are refused.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.macros.runtime import _normalize_replay_kwargs, dispatch_simple
from octowright.macros.substitution import (
    SEMANTIC_LOCATOR_KEYS,
    action_kwargs,
    strip_non_aria_noise,
    substitute,
)

OWN = frozenset({("https", "app.example.test", 443)})
SECRET = {"password": "hunter2"}  # pragma: allowlist secret (synthetic fixture)


def _split(kind: str, first: str, second: str) -> dict[str, Any]:
    return {
        "action": kind,
        "pattern": first,
        "url_pattern": second,
        "headers": {"X-Leak": "{{password}}"},
        **_opt_in(kind, "X-Leak"),
    }


def _opt_in(kind: str, header: str) -> dict[str, Any]:
    """inject_headers needs the per-header redirect opt-in to carry a credential to the own site."""
    return {"forward_on_redirect": {header: True}} if kind == "inject_headers" else {}


@pytest.mark.parametrize("kind", ["inject_headers", "mock_route"])
@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("https://app.example.test/**", "https://evil.test/**"),
        ("https://evil.test/**", "https://app.example.test/**"),
    ],
)
def test_two_spellings_that_disagree_are_refused(kind: str, first: str, second: str) -> None:
    with pytest.raises(ValueError, match="both 'pattern' and 'url_pattern'"):
        substitute([_split(kind, first, second)], SECRET, trusted_origins=OWN)


@pytest.mark.parametrize("kind", ["inject_headers", "mock_route"])
def test_the_substituted_action_carries_only_the_spelling_replay_uses(kind: str) -> None:
    [action] = substitute(
        [
            {
                "action": kind,
                "pattern": "https://app.example.test/**",
                "headers": {"A": "Bearer {{password}}"},
                **_opt_in(kind, "A"),
            }
        ],
        SECRET,
        trusted_origins=OWN,
    )
    assert "pattern" not in action
    assert action["url_pattern"] == "https://app.example.test/**"


@pytest.mark.parametrize("kind", ["inject_headers", "mock_route"])
def test_two_spellings_that_agree_collapse_to_one(kind: str) -> None:
    same = "https://app.example.test/**"
    [action] = substitute([_split(kind, same, same)], SECRET, trusted_origins=OWN)
    assert action["url_pattern"] == same
    assert "pattern" not in action


@pytest.mark.parametrize("kind", ["inject_headers", "mock_route", "uninject_headers", "unmock_route"])
def test_replay_normalization_refuses_disagreeing_spellings_too(kind: str) -> None:
    """A path that reaches dispatch without substitute (a nested body) is refused the same way."""
    with pytest.raises(ValueError, match="both 'pattern' and 'url_pattern'"):
        _normalize_replay_kwargs(kind, {"pattern": "**/a/**", "url_pattern": "**/b/**"})


def test_the_nested_body_path_never_installs_either_pattern() -> None:
    import asyncio

    session = AsyncMock()
    action = _split("inject_headers", "https://app.example.test/**", "https://evil.test/**")
    action["headers"] = {"X-Leak": "hunter2"}  # pragma: allowlist secret (synthetic fixture)

    with pytest.raises(ValueError, match="both"):
        asyncio.run(
            dispatch_simple(
                session,
                action,
                semantic_keys=SEMANTIC_LOCATOR_KEYS,
                strip_non_aria_noise=strip_non_aria_noise,
                action_kwargs=action_kwargs,
            )
        )
    session.inject_headers.assert_not_called()
