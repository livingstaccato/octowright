# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Session-level constants shared between :mod:`session.core` and the mixin
modules under :mod:`session`.

This sub-module exists purely to break the import cycle that would otherwise
form if a mixin (e.g. ``session.core_ops_mixin``) needed to import a constant
from ``session.core`` while ``session.core`` already imports the mixin to
build its dataclass. Keeping the constant in a leaf module that neither side
of the cycle depends on keeps both imports cheap and side-effect free.
"""

from __future__ import annotations

# Maximum characters returned in inline HTML/text previews from session
# diagnostic surfaces (e.g. ``diagnostic_bundle``'s ``html_preview``). Re-
# exported via :mod:`octowright.session` and ``octowright.session.core``.
DEFAULT_PREVIEW_CHARS = 4000

#: Characters of one console message's text kept in the recording and the
#: session's console ring buffer. The page decides how long a message is, and
#: the recorder JSON-serialises and flushes every row synchronously, so a page
#: logging a stringified multi-megabyte API response cost that much memory,
#: event-loop time and disk per call -- with the global recording ceiling off
#: by default. Longer text keeps this prefix plus a marker naming the original
#: length (``text_truncated`` / ``text_length`` on the row).
#: ``MACRO_FAILURE_CONSOLE_TEXT_CHARS`` is the tighter cap for the one payload
#: that rides the MCP transport.
CONSOLE_TEXT_MAX_CHARS = 16_000

#: Bytes of one websocket frame's payload stored in the sidecar, whatever the
#: ``OCTOWRIGHT_WEBSOCKET_MAX_BYTES`` ceiling. Without it one
#: frame from a firehose was copied, base64-expanded (x4/3) and json.dumps'd in
#: full before anything looked at its size. Longer payloads keep this prefix;
#: the row still carries the true ``payload_size`` and ``payload_truncated``.
WEBSOCKET_FRAME_MAX_BYTES = 256 * 1024
#: Longest ``b'...'`` text frame parsed back to bytes. A bytes repr spends up
#: to four chars per byte, so this admits every frame whose decoded bytes could
#: fit ``WEBSOCKET_FRAME_MAX_BYTES``. The text is page-controlled and
#: ``ast.literal_eval`` runs on the event loop: an uncapped parse of a 50-100 MB
#: frame stalled the daemon. A longer frame is kept as (capped) text instead.
BINARY_TEXT_PARSE_MAX_CHARS = 4 * WEBSOCKET_FRAME_MAX_BYTES + 3
