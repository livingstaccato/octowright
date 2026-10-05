# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What the navigation guard last served each frame: a real document or its client-redirect stub.

Split out of :mod:`octowright.ssrf_guard`, which re-exports the public names
(and ``_note_served``, which tests drive directly). A caller that only saw load
states -- ``open_url``'s popup settle, crash recovery -- reads it through
:func:`served_client_redirect_last` to tell the stub from the destination.
"""

from __future__ import annotations

import itertools
import weakref
from typing import Any

from provide.telemetry import get_logger

log = get_logger(__name__)

#: What the guard last served each frame's navigation: ``(seq, state)``.
#: ``state`` is ``"real"`` for a real document, ``"stub"`` for its
#: client-redirect document not yet committed, and ``"stub_committed"`` once
#: it has; the frame's next commit replaces it (:func:`note_frame_navigated`).
#: Per frame, so heavy navigation elsewhere cannot evict a popup's entry;
#: ``seq`` orders it against entries that were parked while their request had
#: no frame (``_SERVED_UNFRAMED``). Read by :func:`served_client_redirect_last`.
_SERVED_LAST: weakref.WeakKeyDictionary[Any, tuple[int, str]] = weakref.WeakKeyDictionary()

#: The same for a navigation whose request had no frame when it was served --
#: a popup's first request (see ``_UNFRAMED``) -- until the frame appears.
#: Bounded like ``_UNFRAMED``.
_SERVED_UNFRAMED: weakref.WeakKeyDictionary[Any, tuple[int, str]] = weakref.WeakKeyDictionary()
_MAX_SERVED_UNFRAMED = 64

_served_seq = itertools.count(1)

#: What the frame's next commit makes of a record (:func:`note_frame_navigated`).
_AFTER_COMMIT = {"stub": "stub_committed", "stub_committed": "real"}


def _record_served(frame: Any, entry: tuple[int, str]) -> None:
    """Record *entry* for *frame* unless a later one is already there."""
    current = _SERVED_LAST.get(frame)
    if current is None or current[0] < entry[0]:
        _SERVED_LAST[frame] = entry


def _adopt_served_unframed() -> None:
    """Hand every parked served entry whose request now has a frame to that frame."""
    for request in list(_SERVED_UNFRAMED):
        try:
            frame = request.frame
        except Exception as exc:  # still no page for it: keep it parked for the next look
            log.debug("octowright.ssrf.served_document_still_unframed", error=repr(exc))
            continue
        entry = _SERVED_UNFRAMED.pop(request, None)
        if entry is not None:
            _record_served(frame, entry)


def _note_served(request: Any, *, client_redirect: bool) -> None:
    entry = (next(_served_seq), "stub" if client_redirect else "real")
    try:
        frame = request.frame
    except Exception:  # a popup's first request: no frame until its page exists
        frame = None
    try:
        if frame is not None:
            _record_served(frame, entry)
            return
        while len(_SERVED_UNFRAMED) >= _MAX_SERVED_UNFRAMED:
            _SERVED_UNFRAMED.pop(next(iter(_SERVED_UNFRAMED)), None)
        _SERVED_UNFRAMED[request] = entry
    except TypeError:  # a frame or request double that cannot be weakly referenced
        log.debug("octowright.ssrf.served_document_untracked")


def note_frame_navigated(frame: Any) -> None:
    """A document committed in *frame*: the stub's own commit, or the document that replaced it.

    Counted, not compared by URL: chromium reports a popup's committed stub as
    ``chrome-error://chromewebdata/`` (measured, Playwright 1.62), so the URL
    does not say which document committed. The commit after the stub's own
    replaces it, whoever served it -- the guard, a service worker the route
    never sees, or the browser's error page for a refused hop. Registered for
    every page of a guarded context (:func:`install_navigation_guard`).
    """
    if _SERVED_UNFRAMED:
        _adopt_served_unframed()
    try:
        entry = _SERVED_LAST.get(frame)
        if entry is not None and entry[1] in _AFTER_COMMIT:
            _SERVED_LAST[frame] = (entry[0], _AFTER_COMMIT[entry[1]])
    except TypeError:  # a frame double that cannot be weakly referenced
        log.debug("octowright.ssrf.frame_navigation_untracked")


def _watch_page(page: Any) -> None:
    try:
        page.on("framenavigated", note_frame_navigated)
    except Exception as exc:
        log.debug("octowright.ssrf.frame_navigation_unwatched", error=repr(exc))


def _unwatch_page(page: Any) -> None:
    try:
        page.remove_listener("framenavigated", note_frame_navigated)
    except Exception as exc:
        log.debug("octowright.ssrf.frame_navigation_unwatch_failed", error=repr(exc))


def served_client_redirect_last(frame: Any) -> bool:
    """Whether *frame* is showing the guard's client-redirect document, as far as the guard knows.

    What a caller that only saw load states cannot tell after the fact: a
    ``domcontentloaded`` it awaited may have been the redirect document's, and
    a page that has since closed can no longer be asked. Readable after the
    page closed (measured on all three engines). True once the guard served
    the frame a client-redirect document, until it serves the frame a real
    one or the frame commits another document after the stub's own
    (:func:`note_frame_navigated`).
    """
    if _SERVED_UNFRAMED:
        _adopt_served_unframed()
    try:
        entry = _SERVED_LAST.get(frame)
    except TypeError:  # a frame double that cannot be weakly referenced
        return False
    return entry is not None and entry[1] != "real"
