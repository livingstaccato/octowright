# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""JSON text that is always encodable as UTF-8, whatever a page put in it.

A JavaScript string is UTF-16 and may hold an unpaired surrogate --
``console.log('\\uD800')``, an accessible name, a websocket frame. Python
carries it as a lone code point, and ``json.dumps(..., ensure_ascii=False)``
passes it through into text that strict UTF-8 then refuses to encode. Every
sink that writes that text -- the recorder, the websocket sidecar, a dashboard
response -- raised on it, losing the row or answering 500.

The fallback is ``ensure_ascii=True`` for that one document: JSON's own
``\\ud800`` escape is valid UTF-8, reads back to the same code point, and
loses nothing. It is chosen over replacing the code point with U+FFFD because
this module sits at the SINK, where the recorder's JSONL is also the input to
replay and export -- rewriting page text there would make a recording
disagree with the page it recorded. Ordinary non-ASCII text keeps the
readable ``ensure_ascii=False`` spelling; only a document that cannot be
encoded pays for the escaped one.

A package-root module, like ``console_levels``, so the recorder can use it
without importing the HTTP layer.
"""

from __future__ import annotations

import json
from typing import Any


def dumps_utf8_safe(obj: Any, **kwargs: Any) -> str:
    """``json.dumps(obj, ensure_ascii=False, **kwargs)``, escaped only if it must be."""
    text = json.dumps(obj, ensure_ascii=False, **kwargs)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(obj, ensure_ascii=True, **kwargs)
    return text
