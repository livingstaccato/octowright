# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The page-level extra HTTP headers each page was given, remembered per page.

Playwright's ``page.set_extra_http_headers`` is per page and has no getter, so
the session keeps what it set: ``header_state`` reports the active page's, and
a replacement carries or names them (``route_carry``). One slot used to hold
them, so headers set on page A and then on page B left A still sending its
headers with nothing recording them.

Kept as ``(page, headers)`` pairs matched by identity rather than a dict
keyed by page: a page is compared by ``is`` everywhere else in the session,
and a closed page's entry is dropped on the next write.
"""

from __future__ import annotations

from typing import Any

_ATTR = "_page_extra_headers_by_page"


def _entries(session: Any) -> list[tuple[Any, dict[str, str]]]:
    entries = getattr(session, _ATTR, None)
    if not isinstance(entries, list):
        entries = []
        setattr(session, _ATTR, entries)
    return entries


def _closed(page: Any) -> bool:
    is_closed = getattr(page, "is_closed", None)
    try:
        return bool(is_closed()) if callable(is_closed) else False
    except Exception:
        return False


def page_headers_on(session: Any, page: Any) -> dict[str, str] | None:
    """The headers set on *page*, or None."""
    for owner, headers in _entries(session):
        if owner is page:
            return headers
    return None


def set_page_headers(session: Any, page: Any, headers: dict[str, str]) -> None:
    """Record *headers* as *page*'s whole map; an empty map clears it, as Playwright does."""
    entries = _entries(session)
    entries[:] = [(owner, kept) for owner, kept in entries if owner is not page and not _closed(owner)]
    if headers:
        entries.append((page, dict(headers)))


def open_pages_with_headers(session: Any) -> list[Any]:
    """The pages still open that have headers set, in the order they were set."""
    return [owner for owner, _headers in _entries(session) if not _closed(owner)]


def move_page_headers(session: Any, old: Any, new: Any) -> None:
    """Record *old*'s headers as *new*'s: *new* replaced *old* and was given them."""
    headers = page_headers_on(session, old)
    if headers is None:
        return
    set_page_headers(session, old, {})
    set_page_headers(session, new, headers)
