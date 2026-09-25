# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A ``JSONResponse`` that cannot 500 on page text.

Starlette's renders with ``ensure_ascii=False`` and a strict UTF-8 encode, so
one console entry holding a lone surrogate made ``/console`` answer 500 until
the entry left the ring. See ``octowright._json_text``.
"""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

from octowright._json_text import dumps_utf8_safe


class SafeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        # Starlette's own arguments, so an encodable body is byte-identical.
        return dumps_utf8_safe(content, allow_nan=False, indent=None, separators=(",", ":")).encode("utf-8")
