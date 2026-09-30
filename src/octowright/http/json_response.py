# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The one JSON response class every HTTP route answers with.

Starlette's ``JSONResponse`` renders with ``ensure_ascii=False`` and a strict
UTF-8 encode, so one console entry holding a lone surrogate made ``/console``
answer 500 until the entry left the ring. See ``octowright._json_text``.

It is a class, used by every route, rather than a helper a route opts into:
the helper was adopted route by route and the scenario and meta routes --
whose bodies carry page text through a macro's console tail and a macro's
recorded accessible names -- were missed. ``tests/test_lone_surrogate_sinks``
fails on any ``http`` module that imports Starlette's class directly.
"""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

from octowright._json_text import dumps_utf8_safe


class SafeJSONResponse(JSONResponse):
    """A ``JSONResponse`` whose body is rendered surrogate-safe.

    Starlette's own ``render`` arguments, so an encodable body is
    byte-identical; still an ``isinstance`` of ``JSONResponse`` for anything
    that checks.
    """

    def render(self, content: Any) -> bytes:
        return dumps_utf8_safe(content, allow_nan=False, indent=None, separators=(",", ":")).encode("utf-8")
