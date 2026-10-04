# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Run a caller-supplied regex over a capture with a hard time bound.

``capture_search(regex=True)`` takes its pattern from the LLM and its text from
a capture that can be megabytes of page content. ``re`` backtracks, and it
holds the GIL for the whole match: a catastrophic pattern such as ``(a+)+$``
froze every thread in the leader -- the MCP transport, the dashboard, browser
event handling -- and nothing in-process can interrupt a running match.

So the match runs in a child interpreter that is killed at
:data:`REGEX_SEARCH_TIMEOUT_SECONDS`. That is the simplest bound that is
actually a bound: a pattern blocklist cannot recognise every super-linear
pattern, a cap on the text only moves the cliff, and the third-party ``regex``
module's timeout would be a new dependency for one tool argument.

Residual limits, stated rather than hidden: every regex search pays an
interpreter start (tens of milliseconds) and holds the capture text in a second
process for its duration; a pattern that needs longer than the bound on a large
capture is refused rather than answered. A literal search (``regex=False``)
compiles an escaped pattern, which is linear, and stays in-process.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 -- fixed argv: this interpreter, a constant script
import sys

from octowright.request_errors import InvalidRequestError

#: Wall-clock bound on one regex search, interpreter start included.
REGEX_SEARCH_TIMEOUT_SECONDS = 5.0

FLAGS = re.IGNORECASE | re.MULTILINE

# Isolated (-I) and without site (-S): stdlib only, nothing from the
# environment or the working directory is imported. Reads UTF-8 JSON as bytes
# so the child's locale cannot change how the text decodes.
_CHILD = (
    "import json, re, sys\n"
    "req = json.loads(sys.stdin.buffer.read())\n"
    "spans = []\n"
    "for m in re.finditer(req['pattern'], req['content'], flags=req['flags']):\n"
    "    spans.append([m.start(), m.end()])\n"
    "    if len(spans) >= req['limit']:\n"
    "        break\n"
    "sys.stdout.write(json.dumps(spans))\n"
)


def regex_match_spans(pattern: str, content: str, *, limit: int) -> list[tuple[int, int]]:
    """``(start, end)`` of up to ``limit`` matches of ``pattern`` in ``content``.

    Raises ``InvalidRequestError`` for a pattern that does not compile or does
    not finish within the bound -- both are the caller's input.
    """
    if limit <= 0:
        return []
    try:
        re.compile(pattern, FLAGS)
    except re.error as exc:
        raise InvalidRequestError(f"invalid regex {pattern!r}: {exc}") from None
    request = json.dumps({"pattern": pattern, "content": content, "flags": int(FLAGS), "limit": limit})
    try:
        proc = subprocess.run(  # nosec B603 -- fixed argv, no shell
            [sys.executable, "-I", "-S", "-c", _CHILD],
            input=request.encode("utf-8"),
            capture_output=True,
            timeout=REGEX_SEARCH_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise InvalidRequestError(
            f"regex {pattern!r} did not finish within {REGEX_SEARCH_TIMEOUT_SECONDS:g}s over this capture "
            "and was stopped; simplify it (avoid nested quantifiers such as (a+)+) or search a literal"
        ) from None
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise RuntimeError(f"regex search failed: {detail[-1] if detail else f'exit {proc.returncode}'}")
    return [(int(start), int(end)) for start, end in json.loads(proc.stdout)]
