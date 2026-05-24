# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared helpers for inspecting wire-format payloads.

Currently only ``looks_like_binary_text`` lives here — the heuristic was
duplicated between ``session/core_io_mixin.py`` (recording side: decide
whether a string payload is actually a ``repr(bytes)`` and base64-encode it
into the JSONL) and ``http/discovery.py::_tail_jsonl`` (replay side:
sanitize ``payload_preview`` fields before pushing them to the dashboard
WebSocket). Both call sites used identical logic; keeping a single source
of truth means a future heuristic change (e.g. checking control-byte
density inside the repr) updates both consumers atomically.
"""

from __future__ import annotations

from typing import Any


def looks_like_binary_text(payload: Any) -> bool:
    """Return ``True`` if ``payload`` is a string that looks like ``repr(bytes)``.

    Playwright surfaces websocket frames as Python objects in the recorder
    layer; if the page sent binary the recorder ends up serialising the
    ``bytes`` value via ``str()`` / ``repr()`` and the resulting payload is
    a string such as ``"b'\\\\x89PNG\\\\r\\\\n...'"``. That string is not
    safe to render in the dashboard or stream to the LLM verbatim — it
    leaks raw bytes through escaping.

    This helper exists so the same shape check runs everywhere the
    binary-as-string smell can show up. The scope is intentionally narrow:
    we only look at the leading and trailing characters (``b"..."`` /
    ``b'...'``), NOT inside the body. A stricter heuristic (non-printable
    byte ratio, control-character density) belongs here too once we find a
    case where the repr-shape check misses; both call sites benefit by
    importing through this module.

    Conservative-by-default behaviour:

    * Non-string input returns ``False`` — only strings can ``"look like"``
      a binary repr.
    * Empty strings return ``False`` (no enclosing quotes to match).
    * Strings without the ``b"..."`` / ``b'...'`` wrapping return ``False``
      even if they contain raw control characters; the recorder path
      handles real ``bytes`` payloads explicitly before reaching this
      check, so a false negative here can only mis-flag a UTF-8 string,
      which is the safer failure mode.
    """
    if not isinstance(payload, str):
        return False
    return (payload.startswith('b"') and payload.endswith('"')) or (payload.startswith("b'") and payload.endswith("'"))
