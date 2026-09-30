# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""HTML to markdown for the per-page markdown cache (``SessionIOMixin.capture_markdown``).

markitdown is optional. These helpers are called from a worker thread, which
is why the converter is cached and locked here rather than built per call.
"""

from __future__ import annotations

import io
import threading
from typing import Any

from provide.telemetry import get_logger

_log = get_logger(__name__)

#: One converter per markitdown module object: ``MarkItDown()`` registers
#: every built-in converter (and probes for magika) on construction, which is
#: wasted work repeated per navigation. Keyed by the module so a test that
#: swaps ``sys.modules["markitdown"]`` gets a converter from the module it
#: installed.
_MARKITDOWN_CONVERTER: tuple[Any, Any] | None = None
#: Held for construction AND conversion. markitdown does not document its
#: converter as thread-safe, and conversions now run in worker threads that
#: several sessions can start at once. Serialising them costs throughput only
#: when two pages convert at the same moment; the event loop stays free either way.
_MARKITDOWN_LOCK = threading.Lock()


def _markitdown_converter(markitdown_mod: Any) -> Any:
    global _MARKITDOWN_CONVERTER
    cached = _MARKITDOWN_CONVERTER
    if cached is not None and cached[0] is markitdown_mod:
        return cached[1]
    converter = markitdown_mod.MarkItDown()
    _MARKITDOWN_CONVERTER = (markitdown_mod, converter)
    return converter


def markitdown_convert(markitdown_mod: Any, html: str) -> Any:
    with _MARKITDOWN_LOCK:
        converter = _markitdown_converter(markitdown_mod)
        stream_info = getattr(markitdown_mod, "StreamInfo", None)
        if stream_info is not None and hasattr(converter, "convert_stream"):
            # convert(str) treats the string as a path or URI (measured on
            # markitdown 0.1.8: FileNotFoundError), so it never converted HTML at
            # all; convert_stream is the HTML-string API.
            return converter.convert_stream(
                io.BytesIO(html.encode("utf-8")), stream_info=stream_info(extension=".html")
            )
        return converter.convert(html)


def rendered_markdown(rendered: Any) -> str:
    """The text of whatever a markitdown version returned."""
    if isinstance(rendered, str):
        return rendered
    if rendered is None:
        raise ValueError("markitdown conversion returned empty result")
    for field in ("text", "markdown", "text_content"):
        candidate = getattr(rendered, field, None)
        if candidate:
            if callable(candidate):
                candidate = candidate()
            text = str(candidate)
            if text.strip():
                return text
    return str(rendered)


#: Characters of page HTML the markdown cache will convert. Capture runs after
#: nearly every load, unasked, and conversion is ~2.7s per MB in the leader
#: every session shares; a remote page building a huge DOM and firing ``load``
#: made the leader serialise, convert and write all of it. Above this the
#: capture is skipped and recorded as a ``markdown_cache_error``. 2 MiB covers
#: ordinary documentation and article pages several times over.
MARKDOWN_CAPTURE_MAX_HTML_CHARS = 2 * 1024 * 1024
#: Measured IN the page, so an oversized DOM is refused before ``content()``
#: copies it into this process. UTF-16 units, close enough to characters.
DOCUMENT_HTML_LENGTH_JS = "() => document.documentElement ? document.documentElement.outerHTML.length : 0"


class MarkdownCaptureTooLarge(RuntimeError):
    """The page's HTML is over ``MARKDOWN_CAPTURE_MAX_HTML_CHARS``; nothing was converted."""


def check_html_size(length: int) -> None:
    if length > MARKDOWN_CAPTURE_MAX_HTML_CHARS:
        _log.info("octowright.markdown.capture_skipped_too_large", html_chars=length)
        raise MarkdownCaptureTooLarge(
            f"page HTML is too large to cache as markdown ({length} chars, limit {MARKDOWN_CAPTURE_MAX_HTML_CHARS})"
        )
