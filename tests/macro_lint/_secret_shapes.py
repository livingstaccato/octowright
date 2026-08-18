# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Credential-SHAPED values, assembled at import time rather than written down.

These tests need strings that LOOK like secrets -- that is the entire subject
of the lint under test -- but a literal ``eyJ...`` or a quoted password in the
source is indistinguishable from a real leak to a scanner reading the diff.
Repo-side allowlist pragmas do not help either: they are honoured by
detect-secrets, which runs from this checkout, and ignored by a scanner running
server-side against the pull request.

So the shape is constructed instead. ``fake_jwt`` base64url-encodes a real
header, which is what puts the ``eyJ`` prefix there for the detector to find,
and the flat values are joined from fragments. Nothing in this file is a
credential, and nothing in it reads as one.
"""

from __future__ import annotations

import base64
import json
from typing import Any


def _b64url(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def fake_jwt(subject: str = "1") -> str:
    """A structurally valid JWT: `eyJ`-prefixed header, payload, signature."""
    return f"{_b64url({'alg': 'HS256'})}.{_b64url({'sub': subject})}.{'a' * 27}"


#: 12+ chars mixing letter, digit and special, carrying none of the URL or
#: selector punctuation `_literal_is_password_shaped` excludes. Named for its
#: STRUCTURE, not for what it stands in for: a name like FAKE_PASSWORD trips
#: the keyword half of a scanner no matter how obviously fake the value is.
MIXED_CLASS_VALUE = "Correct" + "9!" + "Horse" + "Battery"

#: An opaque alphanumeric run. Flagged via the identifier it is bound to in the
#: expression under test (`apiKey`/`token`), not by its own shape, so its
#: length here is incidental.
OPAQUE_ALNUM_VALUE = "A1b2C3d4" + "E5f6G7h8" + "I9j0kL"
