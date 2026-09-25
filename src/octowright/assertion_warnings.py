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
it uses builtins only. A step can ask for either caveat to fail instead
(``require_settled`` / ``require_match``, :func:`strict_refusal`).
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


#: The step option that turns each check's caveat into a failure.
STRICT_OPTIONS = {"expect_network_clean": "require_settled", "expect_no_text": "require_match"}


def strict_option(kind: str, value: Any) -> bool:
    """Validate *kind*'s strictness option: only a real boolean, so ``"false"`` cannot mean on."""
    if not isinstance(value, bool):
        raise ValueError(f"{kind}: {STRICT_OPTIONS[kind]} must be true or false, got {value!r}")
    return value


def strict_refusal(kind: str, observation: dict[str, Any], *, required: bool) -> str | None:
    """The failure for a *kind* check whose step asked for strictness, or None when it may pass.

    ``require_settled`` fails exactly where :func:`assertion_warning` would warn.
    ``require_match`` fails on any selector that matched nothing, ``body``
    included: the default warning spares ``body`` because a page with no body is
    not a stale selector, but a step that asked for a match did not get one.
    """
    if not required:
        return None
    option = STRICT_OPTIONS[kind]
    if kind == "expect_no_text":
        if observation.get("matched") != 0:
            return None
        selector = observation.get("selector", "body")
        return f"expect_no_text: {option} is set and selector {selector!r} matched no element, so no text was checked"
    warning = assertion_warning(kind, observation)
    return None if warning is None else f"{kind}: {option} is set and {warning}"
