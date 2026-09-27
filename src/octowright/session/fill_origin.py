# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential fill bound to the document whose origin was checked.

The macro layer's origin check (``macros.credential_fill``) used to read the
active frame's URL once, before dispatch, and the fill then decided for itself
where the value went. Two things moved it, both measured on all three engines
(``tests/test_macro_credential_fill_frame_live.py``): Playwright's actionability
wait survives a navigation, so a page that moved itself after the check was
filled on the new origin; and a selector that enters a frame
(``iframe >> internal:control=enter-frame >> #pw``) resolves in a child frame
whose origin nobody read.

So while a marked step dispatches, the macro layer binds its check here and
the session's typing methods resolve the element FIRST, hand the check the URL
of the frame that owns it, and type into that same handle. A navigation after
the check detaches the handle and the fill fails instead of following the page.

A context variable, like the macro layer's warn-mode audit, rather than a
parameter threaded through the dispatcher, its conditional recursion and every
test fake: the binding covers exactly one step's dispatch, and all of it runs
in the run's own task. Nothing outside a macro step ever binds one, so a direct
``browser_fill`` is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

#: Called with the owning frame's URL; raises to refuse the fill.
FillOriginCheck = Callable[[str], None]

_CHECK: ContextVar[FillOriginCheck | None] = ContextVar("octowright_fill_origin_check", default=None)


@contextmanager
def fill_origin_check(check: FillOriginCheck | None) -> Iterator[None]:
    """Bind *check* to every fill/type the enclosed dispatch makes; ``None`` binds nothing.

    ``None`` is bound explicitly rather than skipped, so an unmarked step can
    never inherit a check -- or the absence of one -- from anything around it.
    """
    token = _CHECK.set(check)
    try:
        yield
    finally:
        _CHECK.reset(token)


def pending_fill_origin_check() -> FillOriginCheck | None:
    return _CHECK.get()
