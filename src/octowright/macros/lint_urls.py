# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Credential detection for URL-valued macro fields.

Split from ``lint.py`` both to keep that module under the LOC ceiling and
because a URL needs genuinely different rules than a typed value.

The generic password heuristic asks "does this string mix letters, digits and
special characters over 12 chars?" -- a description that every ordinary URL
satisfies, since ``:`` ``/`` ``.`` ``?`` ``=`` are all special. Running it on a
``navigate`` URL warns on essentially every macro, and a check that cries wolf
on the common case gets tuned out before it ever catches the real one.

So a URL is inspected by PART instead of as one blob. Only two parts can
actually carry a secret:

* userinfo -- ``https://admin:hunter2@host/`` is a literal credential by
  construction, no heuristic needed;
* query parameters -- flagged when the parameter NAME is secret-ish
  (``token``, ``api_key``, ``password``, …) or the VALUE is independently
  token-shaped.

The path and host are deliberately never scanned: a path segment is a resource
name, and treating a high-entropy one as a secret re-introduces the same noise
(``/v2/reports/8f3a91c2`` is an id, not a key).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import parse_qsl, urlsplit

# Parameter names that carry a secret when they carry anything at all.
_SECRET_PARAM_RE = re.compile(
    r"(?:^|[_\-.])(?:token|secret|password|passwd|pwd|api[_\-]?key|apikey|auth|credential|session[_\-]?id|sig)"
    r"(?:$|[_\-.])|^(?:token|secret|password|passwd|pwd|apikey|auth|sig)$",
    re.IGNORECASE,
)


def _has_secret_param_name(name: str) -> bool:
    return bool(_SECRET_PARAM_RE.search(name))


def url_carries_credential(raw: str, *, token_like: Callable[[str], bool]) -> bool:
    """True if *raw* embeds a credential in a part that can hold one.

    *token_like* is injected rather than imported so this module stays free of
    a back-import from ``lint``; it receives a single query-parameter VALUE.
    """
    try:
        parsed = urlsplit(raw)
    except ValueError:
        # An unparsable string isn't a URL we can reason about; the caller's
        # generic heuristics are a better judge than a guess here.
        return False

    # Basic-auth userinfo is a credential by construction.
    try:
        if parsed.username or parsed.password:
            return True
    except ValueError:
        # Malformed netloc (e.g. a bad port) — same reasoning as above.
        return False

    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
        if not value:
            continue
        if _has_secret_param_name(name):
            return True
        if token_like(value):
            return True
    return False
