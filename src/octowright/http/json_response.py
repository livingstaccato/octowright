# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A JSON response that cannot 500 on page text.

Starlette's ``JSONResponse`` renders with ``ensure_ascii=False`` and a strict
UTF-8 encode, so one console entry holding a lone surrogate made ``/console``
answer 500 until the entry left the ring. See ``octowright._json_text``.
"""

from __future__ import annotations

from typing import Any

from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from octowright._json_text import dumps_utf8_safe


def safe_json_response(
    content: Any,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    background: BackgroundTask | None = None,
) -> JSONResponse:
    """A ``JSONResponse`` whose body is rendered surrogate-safe.

    Starlette's own ``render`` arguments, so an encodable body is
    byte-identical; the body is replaced after construction rather than by
    overriding ``render`` so the instance is still a plain ``JSONResponse``.
    """
    response = JSONResponse(None, status_code=status_code, headers=headers, background=background)
    response.body = dumps_utf8_safe(content, allow_nan=False, indent=None, separators=(",", ":")).encode("utf-8")
    response.headers["content-length"] = str(len(response.body))
    return response
