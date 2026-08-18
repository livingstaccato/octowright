# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The repair guard must be derived from the SAME key set it guards.

``semantic_replacement`` builds its replacement from the INJECTED
``semantic_keys``, while ``_has_resolvable_finder`` consulted the module-level
``SEMANTIC_FINDER_KEYS`` constant. Two sources for one decision is the exact
drift that produced the bug the guard exists to stop: the guard says "this
action has a finder", the builder copies a narrower set, and the emitted
``click_by`` reaches replay with no finder at all -- ``ValueError: exactly one
of role/label/text/test_id must be set``, with the working CSS selector already
dropped.

Today every caller injects ``SEMANTIC_LOCATOR_KEYS``, so the two agree by
coincidence. These tests pin the derivation so a narrower injection stays
correct instead of becoming a latent replay crash.
"""

from __future__ import annotations

from typing import Any

from octowright.macros.repair import semantic_replacement
from octowright.macros.substitution import SEMANTIC_FINDER_KEYS, SEMANTIC_LOCATOR_KEYS


def _click(**fields: Any) -> dict[str, Any]:
    return {"action": "click", "selector": "#submit", **fields}


def test_narrowed_injection_refuses_a_finder_it_would_not_copy() -> None:
    """`text` is a real finder, but a caller injecting a `text`-less key set
    would not copy it -- so the guard must not accept it either."""
    keys = tuple(k for k in SEMANTIC_LOCATOR_KEYS if k != "text")

    assert semantic_replacement(_click(text="Sign in"), semantic_keys=keys) is None


def test_the_replacement_always_carries_a_finder_when_it_is_built() -> None:
    """The invariant the guard exists for, asserted on the OUTPUT rather than
    on the guard's inputs: whatever is returned is replayable."""
    for dropped in SEMANTIC_FINDER_KEYS:
        keys = tuple(k for k in SEMANTIC_LOCATOR_KEYS if k != dropped)
        out = semantic_replacement(_click(**{dropped: "x"}), semantic_keys=keys)
        assert out is None, f"{dropped!r} was accepted by the guard but is not in the injected keys"

    built = semantic_replacement(_click(label="Email"), semantic_keys=SEMANTIC_LOCATOR_KEYS)
    assert built is not None
    assert any(built.get(k) is not None for k in SEMANTIC_FINDER_KEYS)


def test_a_modifier_only_action_is_still_refused() -> None:
    """The original regression: `text_exact=False` is `is not None`, so a
    truthiness-blind guard turned a modifier into a "finder"."""
    assert semantic_replacement(_click(text_exact=False), semantic_keys=SEMANTIC_LOCATOR_KEYS) is None
