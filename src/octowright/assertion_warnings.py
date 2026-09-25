# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a passing ``expect_network_clean`` / ``expect_no_text`` should still tell someone.

Both checks can pass on less than they were asked to judge: a settle wait that
ended with requests still pending judges without them, and a selector that
matched nothing checked nothing. That is by design -- a long poll never
settles, a stale selector is not a leak -- so neither fails. It is not the same
as a clean pass, though, and nothing said so: ``macro_run`` returned only
counts and the exported CLI printed nothing. Macro replay and the exported CLI
both word it with this one function; the CLI renders its source verbatim, so
it uses builtins only.
"""

from __future__ import annotations

from typing import Any


def assertion_warning(kind: str, observation: dict[str, Any]) -> str | None:
    """A one-line caveat for a passing *kind* check that saw *observation*, or None when it was a clean pass.

    *observation* is the check's own result (counts only, never the forbidden
    text), plus the ``selector`` for ``expect_no_text``.
    """
    if kind == "expect_network_clean":
        caveats = []
        if observation.get("in_flight"):
            caveats.append(
                f"{observation['in_flight']} request(s) still in flight when the settle wait ended were not judged"
            )
        if observation.get("in_flight_untracked"):
            caveats.append(
                f"{observation['in_flight_untracked']} request(s) were dropped from in-flight tracking "
                "and may still have been running"
            )
        return "; ".join(caveats) or None
    if kind == "expect_no_text":
        selector = observation.get("selector", "body")
        if observation.get("matched") == 0 and selector != "body":
            return f"selector {selector!r} matched no element, so no text was checked"
    return None
