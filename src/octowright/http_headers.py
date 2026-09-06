# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Validation and record-time redaction for caller-supplied HTTP headers.

Lives at the package root, like :mod:`octowright.console_levels`, because both
``browser_pool.options`` (launch-time, context-level) and ``session`` (the
page-level macro action) need the same rules, and a shared helper in either
one would make the other import a layer it has no business importing.
"""

from __future__ import annotations

import re
from typing import Any

from octowright.request_errors import InvalidRequestError
from octowright.url_patterns import validate_url_pattern

# Bounds on a caller-supplied header map. It rides EVERY request the browser
# makes, so it is capped rather than trusted to be small.
MAX_EXTRA_HTTP_HEADERS = 32
MAX_EXTRA_HTTP_HEADER_VALUE_CHARS = 4096

# Bounds on the URL globs that SCOPE a header map. Each one becomes a context
# route, so the count is capped for the same reason the header map is.
MAX_EXTRA_HTTP_HEADER_URLS = 32
MAX_EXTRA_HTTP_HEADER_URL_CHARS = 2048

# RFC 7230 token: the characters a header NAME may legally contain.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# What a redacted header value is replaced with in the JSONL recording. Distinct
# from REDACTED_INPUT_PLACEHOLDER so a reader can tell WHICH scrubber ran, and so
# replay can refuse it with an accurate message.
REDACTED_HEADER_PLACEHOLDER = "<redacted:header>"

# Header names whose value is a credential. Unlike ``press_key``/``evaluate`` --
# selector-less sinks that genuinely cannot classify their own value, and so are
# scrubbed only under the blanket ``all`` mode -- a header carries its NAME, and
# the name says whether the value is a secret. That makes these classifiable
# under the DEFAULT ``passwords`` policy, exactly as an ``<input type=password>``
# is classifiable by its element type.
_CREDENTIAL_HEADER_NAMES = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie"})
_CREDENTIAL_HEADER_HINTS = ("token", "secret", "api-key", "apikey", "auth", "password", "credential", "session")


def is_credential_header(name: str) -> bool:
    """Whether a header name marks its value as a credential."""
    lowered = name.strip().lower()
    return lowered in _CREDENTIAL_HEADER_NAMES or any(hint in lowered for hint in _CREDENTIAL_HEADER_HINTS)


def validate_one_header(name: Any, value: Any) -> None:
    """Reject a header that would forge a request rather than decorate one.

    A CR or LF in a value is header injection: it ends the header and starts
    another, so one "value" can append a header the caller never wrote.
    """
    if not isinstance(name, str) or not _HEADER_NAME_RE.match(name):
        raise InvalidRequestError(f"invalid HTTP header name: {name!r}")
    if not isinstance(value, str):
        raise InvalidRequestError(f"header {name!r} must have a string value, got {type(value).__name__}")
    if len(value) > MAX_EXTRA_HTTP_HEADER_VALUE_CHARS:
        raise InvalidRequestError(f"header {name!r} exceeds {MAX_EXTRA_HTTP_HEADER_VALUE_CHARS} chars")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise InvalidRequestError(f"header {name!r} value contains a control character (header injection)")


def validate_extra_http_headers(headers: Any) -> None:
    """Validate a whole header map, or raise ``ValueError`` naming the offender."""
    if headers is None:
        return
    if not isinstance(headers, dict):
        raise InvalidRequestError("extra_http_headers must be a mapping of header name to value")
    if len(headers) > MAX_EXTRA_HTTP_HEADERS:
        raise InvalidRequestError(f"extra_http_headers accepts at most {MAX_EXTRA_HTTP_HEADERS}, got {len(headers)}")
    for name, value in headers.items():
        validate_one_header(name, value)


def redact_headers_for_report(headers: dict[str, str]) -> dict[str, str]:
    """Header map as it should be REPORTED over the tool/HTTP surface.

    Shares the recorder's allow/deny logic -- the same header-NAME
    classification, via :func:`redact_header_values` -- and differs only in the
    mode floor: ``off`` is treated as ``passwords``.

    That divergence is deliberate and is the stricter direction.
    ``OCTOWRIGHT_REDACT_INPUTS=off`` is documented as an opt-in for
    RECORDINGS, which are a ``0600`` file on the operator's own disk. This
    surface is not that: it crosses the MCP transport to any connected client
    and lands in an LLM's context. Honouring ``off`` here would silently turn a
    recording-privacy setting into "ship my bearer token to every caller",
    which nobody setting that variable is asking for. ``all`` is still honoured
    because it is stricter than the floor, not looser.

    Names are never scrubbed either way: which headers a browser sets is the
    diagnostic value, and the name is not the secret.
    """
    from octowright.session.aria_redaction import resolve_redaction_mode

    mode = resolve_redaction_mode()
    return redact_header_values(headers, "passwords" if mode == "off" else mode)


def validate_extra_http_header_urls(url_patterns: Any) -> None:
    """Validate the URL globs that scope a header map, or raise ``ValueError``.

    Unvalidated, this reached ``context.route`` verbatim from both the HTTP
    ``POST /api/sessions`` body and the MCP ``browser_launch`` args. A bare
    STRING is the dangerous shape: it iterates CHARACTERS, so ``"**/api/**"``
    registers one route per character -- ``*``, ``/``, ``a`` -- and sprays the
    header at unrelated origins, the exact opposite of what scoping is for.

    An empty list is refused rather than treated as "no scoping". ``[]`` most
    naturally reads as "scope to nothing", and the truthiness check it used to
    meet read it as "send them everywhere"; for a security-adjacent knob,
    failing open in the credential-spraying direction is the wrong way to be
    wrong. Omit the argument for deliberate context-level headers.
    """
    if url_patterns is None:
        return
    if isinstance(url_patterns, str) or not isinstance(url_patterns, list | tuple):
        raise InvalidRequestError("extra_http_headers_urls must be a list of URL glob patterns")
    if not url_patterns:
        raise InvalidRequestError(
            "extra_http_headers_urls must be non-empty -- omit it entirely for context-level headers"
        )
    if len(url_patterns) > MAX_EXTRA_HTTP_HEADER_URLS:
        raise InvalidRequestError(
            f"extra_http_headers_urls accepts at most {MAX_EXTRA_HTTP_HEADER_URLS}, got {len(url_patterns)}"
        )
    for pattern in url_patterns:
        if not isinstance(pattern, str):
            raise InvalidRequestError(
                f"extra_http_headers_urls entries must be string patterns, got {type(pattern).__name__}"
            )
        if not pattern.strip():
            raise InvalidRequestError("extra_http_headers_urls entries must not be empty")
        if len(pattern) > MAX_EXTRA_HTTP_HEADER_URL_CHARS:
            raise InvalidRequestError(f"URL pattern exceeds {MAX_EXTRA_HTTP_HEADER_URL_CHARS} chars")
        # A length cap alone does not bound the MATCH cost: the measured
        # attack is eighteen characters. These globs become context routes
        # exactly like `inject_headers`, so they need the same wildcard bound.
        validate_url_pattern(pattern, field="extra_http_headers_urls")


def redact_header_values(headers: dict[str, str], mode: str) -> dict[str, str]:
    """Header map as it should be RECORDED. The browser always gets the real one.

    ``all`` scrubs every value; ``passwords`` (the default) scrubs the ones the
    name marks as credentials; ``off`` records verbatim. Names are never
    scrubbed -- which headers a run set is the diagnostic value here, and the
    name is not the secret.
    """
    if mode == "off":
        return dict(headers)
    scrub_all = mode == "all"
    return {
        name: REDACTED_HEADER_PLACEHOLDER if scrub_all or is_credential_header(name) else value
        for name, value in headers.items()
    }
