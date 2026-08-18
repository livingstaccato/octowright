# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Credential detection for URL- and code-valued macro fields.

Split from ``lint.py`` both to keep that module under the LOC ceiling and
because a URL needs genuinely different rules than a typed value.

The generic password heuristic asks "does this string mix letters, digits and
special characters over 12 chars?" -- a description that every ordinary URL
satisfies, since ``:`` ``/`` ``.`` ``?`` ``=`` are all special. Running it on a
``navigate`` URL warns on essentially every macro, and a check that cries wolf
on the common case gets tuned out before it ever catches the real one.

So a URL is inspected by PART. Which parts can hold a secret is a question
about protocols, not about taste, and the first version of this module got it
wrong by asserting only userinfo and query could:

* **userinfo** -- ``https://admin:hunter2@host/`` is a literal credential by
  construction, no heuristic needed;
* **query** -- flagged on a secret-ish parameter NAME or a token-shaped VALUE;
* **fragment** -- the OAuth 2.0 implicit grant returns ``access_token`` here BY
  SPECIFICATION, precisely to keep it out of server logs, and OIDC returns
  ``id_token`` the same way. Skipping it meant a macro recorded through any
  SPA/OAuth callback wrote a live bearer token into shared macro JSON with a
  clean lint report;
* **path** -- magic-link and password-reset tokens are path segments. Scanned
  with the known-prefix signal ONLY: a path segment is normally a resource
  name, so a shape/entropy test there re-introduces the noise this module
  exists to remove (``/v2/reports/8f3a91c2`` is an id, not a key).

The host is never scanned -- a hostname is public by definition.

**Value shape.** The previous value signal was Shannon entropy over 20+ chars,
which fails in both directions: max entropy over a 16-symbol alphabet is
exactly ``log2(16) == 4.0`` against a strict ``> 4.0``, so it could never fire
on a hex digest, while ordinary long values (a percent-encoded ``redirect_uri``,
a human-readable ``title``) cleared it easily. It is replaced by a structural
test -- an opaque run of alphanumerics with no URL or human punctuation in it --
which is what actually distinguishes a token from a sentence or a nested URL.

**Parameter names** come from the pre-existing
``octowright.artifacts.redaction.is_sensitive_key`` rather than a fourth
private regex (``artifacts/redaction``, ``artifacts/evidence``,
``artifacts/script_export`` and ``advisor`` each had their own). It imports
only stdlib, so there is no cycle, and it already handles camelCase,
plurals and the spellings the local regex missed (``accessToken``,
``passphrase``, ``authorization``, ``private_key``, ``cookie``).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from octowright.artifacts.redaction import is_sensitive_key

from .lint_credentials import _TOKEN_PREFIX_RE, _TOKEN_PREFIX_SEARCH_RE

#: Query-string idioms `is_sensitive_key` does not cover, because they are only
#: secret-ish in a URL. Kept deliberately small; anything general belongs in
#: `is_sensitive_key` so every caller benefits.
_URL_PARAM_SECRET_NAMES = frozenset({"key", "sig", "signature", "jwt", "bearer", "csrf", "sid", "sessionid"})

#: An opaque token: one unbroken run of alphanumerics. Any `.`, `/`, `%`, `:`
#: or space means the value has URL or human structure and is not a bare token.
#: Hyphens and underscores are excluded too, which is what keeps a UUID and a
#: `summer-2026-launch-promo-email` campaign slug out of the net.
_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{24,}$")
#: A hex digest/key is opaque even when it happens to contain no letters.
_HEX_SECRET_RE = re.compile(r"^[0-9a-fA-F]{32,}$")
_HAS_DIGIT = re.compile(r"\d")
_HAS_LETTER = re.compile(r"[A-Za-z]")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _param_name_is_secret(name: str) -> bool:
    return is_sensitive_key(name) or _NON_ALNUM.sub("", name.lower()) in _URL_PARAM_SECRET_NAMES


def _value_is_token_shaped(value: str) -> bool:
    """Structural test for "this is an opaque secret, not a word or a URL"."""
    if _TOKEN_PREFIX_RE.match(value):
        return True
    if _HEX_SECRET_RE.match(value):
        return True
    if not _OPAQUE_TOKEN_RE.match(value):
        return False
    # A 24+ char run of pure letters is a word or a slug; a token mixes classes.
    return bool(_HAS_DIGIT.search(value) and _HAS_LETTER.search(value))


def _params_carry_credential(blob: str) -> bool:
    """Scan a ``a=b&c=d`` blob — used for both the query and the fragment."""
    return any(_param_name_is_secret(name) or _value_is_token_shaped(value) for name, value in parse_qsl(blob))


def _path_carries_credential(path: str) -> bool:
    """Known token prefixes only — see the module docstring on path noise."""
    return any(_TOKEN_PREFIX_RE.match(segment) for segment in path.split("/") if segment)


def url_carries_credential(raw: str) -> bool | None:
    """True if *raw* embeds a credential in a part that can hold one.

    ``None`` means "this is not a URL I can parse", which is NOT the same as
    "no credential". The previous version returned ``False`` there while its
    comment claimed the caller's generic heuristics were a better judge -- but
    the caller returned that ``False`` verbatim, so a literal basic-auth
    credential in a slightly malformed URL (``https://admin:hunter2@[bad/x``,
    which raises ``ValueError: Invalid IPv6 URL``) was silently accepted by a
    credential linter. Returning ``None`` makes the documented fallback real.
    """
    try:
        parsed = urlsplit(raw)
        # .username/.password re-parse the netloc lazily, so a malformed one
        # raises here rather than above.
        userinfo = bool(parsed.username or parsed.password)
    except ValueError:
        return None

    if userinfo:
        return True
    if _params_carry_credential(parsed.query):
        return True
    if _params_carry_credential(parsed.fragment) or _TOKEN_PREFIX_SEARCH_RE.search(parsed.fragment):
        return True
    return _path_carries_credential(parsed.path)


def code_carries_credential(expression: str) -> bool:
    """True if a JS expression has a known credential shape embedded in it.

    A JS expression is not a URL and not a password, so neither of the other
    detectors describes it. Only the high-precision prefix signal is used: the
    shape heuristics would fire on ordinary code (``document.querySelector(…)``
    is 12+ characters mixing letters, digits and punctuation).
    """
    return bool(_TOKEN_PREFIX_SEARCH_RE.search(expression))
