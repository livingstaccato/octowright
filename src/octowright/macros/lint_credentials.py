# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pure credential-shape heuristics used by ``macro_lint``.

Split out of ``lint.py`` to keep that module under the repo's per-file line
budget. These are deliberately side-effect free and ``Issue``-free: ``lint.py``
owns the decision to *report*, this module only answers "does this string look
like a secret?". ``lint_urls`` layers URL-part-aware detection on top of
``_token_like``.
"""

from __future__ import annotations

import math
import re

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.\w+$")
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}")
_HAS_DIGIT = re.compile(r"\d")
_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_SPECIAL = re.compile(r"[^A-Za-z0-9]")

# Well-known credential prefixes — small, high-precision set that catches
# vendor token shapes the digit+letter+special heuristic misses (no special
# characters, or short overall). Tuned for false-negative reduction, not
# exhaustive coverage; the runtime ``OCTOWRIGHT_REDACT_INPUTS`` policy is the
# real defence.
# The vendor-prefix alternation is defined ONCE and both regexes build from it:
# an anchored form (is this whole value a token?) and a search form (does a
# token appear anywhere inside this blob, e.g. a JS expression). Two
# hand-maintained copies would drift the moment a vendor prefix is added.
_VENDOR_PREFIX_ALTERNATION = (
    r"AKIA[0-9A-Z]{16}"  # AWS access key ID
    r"|ghp_[A-Za-z0-9]{20,}"  # GitHub personal access token
    r"|gho_[A-Za-z0-9]{20,}"  # GitHub OAuth token
    r"|ghu_[A-Za-z0-9]{20,}"  # GitHub user-to-server token
    r"|ghs_[A-Za-z0-9]{20,}"  # GitHub server-to-server token
    r"|ghr_[A-Za-z0-9]{20,}"  # GitHub refresh token
    r"|github_pat_[A-Za-z0-9_]{20,}"  # GitHub fine-grained PAT
    r"|glpat-[A-Za-z0-9_-]{20,}"  # GitLab personal access token
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"  # Slack tokens
    r"|ya29\.[A-Za-z0-9_-]{20,}"  # Google OAuth access token
    r"|sk-[A-Za-z0-9]{20,}"  # OpenAI / Anthropic-style secret key
    r"|(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{20,}"  # Stripe API keys
)

_TOKEN_PREFIX_RE = re.compile(rf"^(?:{_VENDOR_PREFIX_ALTERNATION})$")
_TOKEN_PREFIX_SEARCH_RE = re.compile(rf"(?:^|[^A-Za-z0-9_-])(?:{_VENDOR_PREFIX_ALTERNATION})(?![A-Za-z0-9_-])")


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


# Fields that are NEVER credentials even if they happen to look like one
# (e.g. a CSS selector containing `[id="user@host"]` — unlikely, but cheap
# to skip). We only inspect string fields whose KEY plausibly carries
# user-supplied values.
_CREDENTIAL_CANDIDATE_KEYS: frozenset[str] = frozenset(
    {"value", "text", "url", "expression", "pattern", "body", "key", "prompt_text"}
)


def _looks_like_password(s: str) -> bool:
    """True if *s* looks like a literal credential.

    Combines three independent signals:
      1. Known token prefixes (AWS, GitHub, Slack, Google OAuth, sk-...).
      2. Classic password shape: >=12 chars with digits + letters + special.
      3. High Shannon entropy (>4.0 bits/char) for strings >=20 chars — catches
         bare hex API keys and alphanumeric bearer tokens that have no special
         characters and so slip past (2).
    """
    if _TOKEN_PREFIX_RE.match(s):
        return True
    if len(s) >= 12 and _HAS_DIGIT.search(s) and _HAS_LETTER.search(s) and _HAS_SPECIAL.search(s):
        return True
    return len(s) >= 20 and _shannon_entropy(s) > 4.0


def _looks_like_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(s))


def _is_placeholder(s: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(s))
