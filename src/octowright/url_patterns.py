# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Bounds on a caller-supplied URL glob before Playwright compiles it.

Playwright converts a route glob to a regex and matches it against every
intercepted request. ``**`` becomes ``(.*)`` and ``*`` becomes ``([^/]*)``, so
``**a**a**b`` compiles to ``^(.*)a(.*)a(.*)b$``. No quantifier is ever nested
inside another, so the blow-up is polynomial rather than exponential -- but the
exponent is one per wildcard and the caller chooses it. Measured against a
129-character URL:

    k=2   0.0014s
    k=3   0.0394s
    k=4   0.9478s
    k=5  18.0107s

That is an eighteen-character pattern taking eighteen seconds, and three
properties make it worse than a slow call:

* The match runs **in the Node driver**, which `BrowserPool` shares across every
  session, so a pattern installed on one browser stalls navigation in all of
  them. The leader's own event loop keeps running, which is why nothing in
  Python noticed.
* The cost is paid **per intercepted request**, so an ordinary page multiplies
  it by its subresource count.
* Playwright's own ``timeout=`` is enforced inside that same wedged driver, so
  it cannot fire to end the stall.

``session/timeouts.bounded`` therefore cannot cover this -- it bounds an awaited
Python call, and this stall is not one. Refusing the pattern *before* forwarding
it is sufficient exactly because the wedge is driver-side: a pattern octowright
never sends is never compiled by anyone.

The pattern reaches here from three places, and two of them are inputs this
repo already treats as untrusted: an MCP tool argument (`browser_mock_route`,
`browser_inject_headers`), a **macro** action -- the same threat model that
makes ``OCTOWRIGHT_MACRO_CREDENTIAL_SINKS`` default to ``block`` -- and a
**scenario YAML** ``fixtures.mock_routes[].pattern``.
"""

from __future__ import annotations

import re

from octowright.request_errors import InvalidRequestError

# Generous against real globs and far below the cliff. `**/api/**/users` uses
# two; the measured cost at four is ~0.9s and at five ~18s, so the accepted
# worst case stays under a second. Raising this re-opens a 20x-per-wildcard
# curve, which `tests/test_url_pattern_guard.py` asserts against directly
# rather than trusting this comment.
MAX_URL_PATTERN_WILDCARDS = 5

# A long pattern costs on every intercepted request even without wildcards.
# Matches the bound `http_headers` already applies to header-scoping globs.
MAX_URL_PATTERN_CHARS = 2048

# Runs, not individual stars: `*` and `**` each contribute exactly one
# capturing group to the compiled regex, so `*a*a*a…` blows up identically to
# `**a**a**a…`. Counting `**` alone would leave that variant unguarded.
_WILDCARD_RUN_RE = re.compile(r"\*+")


def validate_url_pattern(pattern: str, *, field: str) -> None:
    """Raise ``ValueError`` if ``pattern`` is too costly to compile and match.

    ``field`` names the caller's argument so the message points at the file to
    edit -- one validator serves the MCP tools, macro replay, and scenario
    YAML, and "url_pattern" would send a scenario author to the wrong place.
    """
    if len(pattern) > MAX_URL_PATTERN_CHARS:
        raise InvalidRequestError(f"{field}: URL pattern exceeds {MAX_URL_PATTERN_CHARS} chars (got {len(pattern)})")
    wildcards = len(_WILDCARD_RUN_RE.findall(pattern))
    if wildcards > MAX_URL_PATTERN_WILDCARDS:
        raise InvalidRequestError(
            f"{field}: URL pattern uses {wildcards} wildcard groups, more than the "
            f"{MAX_URL_PATTERN_WILDCARDS} allowed. Playwright compiles each one into a "
            f"regex group whose match cost multiplies about 20x per wildcard, in the "
            f"driver process shared by every browser in the pool. Narrow the pattern "
            f"(e.g. '**/api/**/users' rather than '**a**a**a**a**a**b')."
        )
