# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from playwright.async_api import Locator

    from octowright.session._protocols import SessionLike


async def build_locator(
    session: SessionLike,
    *,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    label_exact: bool = False,
    text: str | None = None,
    text_exact: bool = False,
    test_id: str | None = None,
) -> Locator:
    """Resolve one semantic finder to a Playwright locator.

    The ``*_exact`` flags are modifiers, not finders: they never satisfy the
    one-finder requirement below, and each defaults to False so the matching
    stays substring-based exactly as Playwright does by default.

    ``exact`` changes TWO things at once, because Playwright ties them
    together: ``escape_for_text_selector`` renders the selector with an ``s``
    suffix when exact and an ``i`` suffix otherwise, so exact matching is also
    CASE-SENSITIVE matching. A caller that flips ``text_exact=True`` purely to
    stop ``"Ada"`` matching ``"Ada Lovelace (old)"`` simultaneously loses the
    case-insensitivity, and ``text="submit", text_exact=True`` does not
    match ``<button>Submit</button>``.
    """
    provided = [k for k, v in (("role", role), ("label", label), ("text", text), ("test_id", test_id)) if v is not None]
    if len(provided) != 1:
        raise ValueError(f"exactly one of role/label/text/test_id must be set; got: {provided}")
    # Target selection (active_frame vs. top-level page) is mutable session
    # state, so it must be resolved under the same lease direct callers of
    # this helper would otherwise bypass -- not just inside the already-gated
    # public click_by/fill_by/get_text_by methods.
    async with session.operation("session_locator_resolve"):
        target = session._target()
        if role is not None:
            kwargs: dict[str, Any] = {}
            if role_name is not None:
                kwargs["name"] = role_name
                kwargs["exact"] = role_exact
            return target.get_by_role(cast(Any, role), **kwargs)
        if label is not None:
            return target.get_by_label(label, exact=label_exact)
        if text is not None:
            return target.get_by_text(text, exact=text_exact)
        assert test_id is not None  # nosec B101
        return target.get_by_test_id(test_id)
