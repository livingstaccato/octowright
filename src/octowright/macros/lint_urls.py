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

from octowright.artifacts.redaction import _NON_ALNUM, is_sensitive_key

from .lint_credentials import (
    _HAS_DIGIT,
    _HAS_LETTER,
    _HAS_SPECIAL,
    _TOKEN_PREFIX_RE,
    _TOKEN_PREFIX_SEARCH_RE,
)

#: Query-string idioms `is_sensitive_key` does not cover, because they are only
#: secret-ish in a URL. Kept deliberately small; anything general belongs in
#: `is_sensitive_key` so every caller benefits.
_URL_PARAM_SECRET_NAMES = frozenset({"key", "sig", "signature", "jwt", "bearer", "csrf", "sid", "sessionid"})

#: Exact `is_sensitive_key` matches that name an IDENTITY, not a secret.
#: Reusing that helper was right for camelCase/plural coverage but imported its
#: `email`/`username` entries into URL parameter matching, where they mean
#: something else: a TYPED field called `email` is half of a credential pair,
#: while `?email=` in a URL is a prefilled signup/invite/unsubscribe link --
#: among the most common links a macro navigates to. Warning on every save of
#: every one of them is precisely the cry-wolf this module exists to remove.
#: The VALUE side is untouched, so `?email=<token-shaped>` still fires.
_URL_PARAM_IDENTITY_NAMES = frozenset({"email", "username"})

#: An opaque token: one unbroken run of alphanumerics. Any `.`, `/`, `%`, `:`
#: or space means the value has URL or human structure and is not a bare token.
#: Hyphens and underscores are excluded too, which is what keeps a UUID and a
#: `summer-2026-launch-promo-email` campaign slug out of the net.
_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{24,}$")
#: A hex digest/key is opaque even when it is mostly digits -- but a 32+ run of
#: characters that are ALL digits is an order id, an epoch-nanos timestamp or a
#: concatenated numeric key far more often than a digest (a real digest comes
#: out all-digits with probability (10/16)**32, about 1e-7). So the charset
#: test is paired with `_HAS_HEX_LETTER`, mirroring the letter+digit mix the
#: opaque-run branch below already demands, and costing no true positives.
_HEX_SECRET_RE = re.compile(r"^[0-9a-fA-F]{32,}$")
_HAS_HEX_LETTER = re.compile(r"[a-fA-F]")
#: A JWT — the shape `_OPAQUE_TOKEN_RE` structurally cannot see, because
#: base64url puts `.`, `-` and `_` inside the token. It is also the shape this
#: module's docstring names as the reason the fragment is scanned at all (the
#: OAuth implicit grant returns one there BY SPECIFICATION), so missing it
#: defeated the fragment rule for every token that wasn't under a `*_token`
#: parameter name. Anchored on the `eyJ` header prefix (base64url of `{"`),
#: which is what keeps this from matching an ordinary dotted identifier.
_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*$")
_JWT_SEARCH_RE = re.compile(r"(?:^|[^A-Za-z0-9_.-])(eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*)")

#: Quoted string literals inside a JS expression, with the identifier (if any)
#: they are assigned to or compared against.
_JS_NAMED_LITERAL_RE = re.compile(r"""([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:={1,3}|:)\s*(['"])(.*?)\2""")
_JS_LITERAL_RE = re.compile(r"""(['"])(.*?)\1""")

#: Path segments that announce "the NEXT segment is a token". A magic-link,
#: password-reset or email-verification URL is the documented reason the path
#: is scanned at all, and its token is an opaque segment with no vendor prefix
#: — indistinguishable BY SHAPE from a resource id, which is why the shape test
#: runs only where one of these words has already said what follows is a
#: secret. Deliberately verbs and nouns of the credential flow, not generic
#: routing words: `/api/` or `/v2/` would put the whole site back in scope.
_CREDENTIAL_PATH_CONTEXT = frozenset(
    {
        "reset",
        "resetpassword",
        "passwordreset",
        "forgot",
        "verify",
        "verifyemail",
        "confirm",
        "confirmation",
        "activate",
        "activation",
        "magic",
        "magiclink",
        "invite",
        "invitation",
        "unsubscribe",
        "token",
        "signin",
    }
)

#: Characters that a selector, URL, sentence or code fragment carries and a
#: password essentially never does. The generic password heuristic ("12+ chars
#: mixing letter/digit/special") is true of `#main .row > td:nth-child(2)`, so
#: without this exclusion it fires on the ordinary contents of an `evaluate`.
_STRUCTURAL_CHARS = frozenset(" \t\r\n#.>:/[](){}<>,;=|&?%\\'\"`")


def _param_name_is_secret(name: str) -> bool:
    compact = _NON_ALNUM.sub("", name.lower())
    if compact in _URL_PARAM_IDENTITY_NAMES:
        return False
    return is_sensitive_key(name) or compact in _URL_PARAM_SECRET_NAMES


def _value_is_token_shaped(value: str) -> bool:
    """Structural test for "this is an opaque secret, not a word or a URL"."""
    if _TOKEN_PREFIX_RE.match(value) or _JWT_RE.match(value):
        return True
    if _HEX_SECRET_RE.match(value) and _HAS_HEX_LETTER.search(value):
        return True
    if not _OPAQUE_TOKEN_RE.match(value):
        return False
    # A 24+ char run of pure letters is a word or a slug; a token mixes classes.
    return bool(_HAS_DIGIT.search(value) and _HAS_LETTER.search(value))


def _literal_is_password_shaped(value: str) -> bool:
    """The classic password shape, minus everything that is really punctuation.

    ``len >= 12 and digit and letter and special`` describes a password AND a CSS
    selector AND a URL AND most sentences — which is why running it on a whole
    ``expression`` blob was pure noise. Applied to a single quoted literal with
    the structural characters excluded, what is left is a password.
    """
    if len(value) < 12 or _STRUCTURAL_CHARS & set(value):
        return False
    return bool(_HAS_DIGIT.search(value) and _HAS_LETTER.search(value) and _HAS_SPECIAL.search(value))


def _params_carry_credential(blob: str) -> bool:
    """Scan a ``a=b&c=d`` blob — used for both the query and the fragment."""
    return any(_param_name_is_secret(name) or _value_is_token_shaped(value) for name, value in parse_qsl(blob))


def _path_carries_credential(path: str) -> bool:
    """Unambiguous shapes anywhere; an opaque token only in a credential context.

    The module docstring justifies scanning the path by naming magic-link and
    password-reset tokens — and then scanned with the vendor-prefix signal
    ONLY, which no magic-link token carries. The branch could never fire on the
    one thing it existed for.

    The reason it was written that way is real: a path segment is normally a
    resource name, so an unqualified shape test flags ``/v2/reports/8f3a91c2``
    as a key. The missing discriminator is the PRECEDING segment. ``/reset/``,
    ``/verify/``, ``/invite/`` announce that what follows is a token, so the
    shape test is scoped to exactly there. Two shapes need no context at all: a
    vendor prefix and a JWT are self-identifying wherever they appear.
    """
    previous = ""
    for segment in (s for s in path.split("/") if s):
        if _TOKEN_PREFIX_RE.match(segment) or _JWT_RE.match(segment):
            return True
        if _NON_ALNUM.sub("", previous.lower()) in _CREDENTIAL_PATH_CONTEXT and _value_is_token_shaped(segment):
            return True
        previous = segment
    return False


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
    # The fragment is scanned as a param blob AND as a bare value: an implicit
    # -grant callback often lands as `#<token>` with no `name=` at all, which
    # parse_qsl reports as zero pairs.
    if _params_carry_credential(parsed.fragment):
        return True
    if _TOKEN_PREFIX_SEARCH_RE.search(parsed.fragment) or _JWT_SEARCH_RE.search(parsed.fragment):
        return True
    return _path_carries_credential(parsed.path)


def code_carries_credential(expression: str) -> bool:
    """True if a JS expression has a credential embedded in it.

    A JS expression is not a URL and not a password, so neither of the other
    detectors describes the blob as a whole — running ``_looks_like_password``
    over it fired on ordinary code (``document.querySelector(…)`` is 12+
    characters mixing letters, digits and punctuation). But narrowing to the
    vendor-prefix signal alone left ``evaluate``/``expect_js`` with no detection
    at all for anything that isn't a recognised vendor token, and
    ``OCTOWRIGHT_REDACT_INPUTS`` only scrubs ``evaluate`` under ``all`` — so in
    the default ``passwords`` mode a cleartext secret was on disk AND unflagged.

    So the expression is inspected the way a URL is: by the parts that can hold
    a secret. Those are its quoted string LITERALS, judged on the same value
    shapes a query parameter is (plus the password shape, which is meaningful
    once it is applied to a literal rather than to surrounding syntax), and on
    the identifier they are bound to — the code analogue of a secret-ish
    parameter name.
    """
    if _TOKEN_PREFIX_SEARCH_RE.search(expression):
        return True
    for name, _quote, literal in _JS_NAMED_LITERAL_RE.findall(expression):
        if literal and is_sensitive_key(name):
            return True
    return any(
        _value_is_token_shaped(literal) or _literal_is_password_shaped(literal)
        for _quote, literal in _JS_LITERAL_RE.findall(expression)
    )
