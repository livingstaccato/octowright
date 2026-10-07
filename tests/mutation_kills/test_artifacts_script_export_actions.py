# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What the exported macro CLI is built from: the rendered network helpers and the step refusal.

``_network_helpers`` runs once, at import, to build ``STATE_HELPERS``; it is
called again here so a change to how it renders is noticed by a test rather
than only by a script that happens to read the changed name.
"""

from __future__ import annotations

import inspect

import pytest

from octowright import request_failures
from octowright.artifacts.script_export_actions import (
    STATE_HELPERS,
    _indent_block,
    _network_helpers,
    refuse_unrunnable_steps,
)
from octowright.assertion_warnings import STRICT_OPTIONS, assertion_warning, strict_option, strict_refusal


def test_network_helpers_render_the_ledger_constants_then_its_source() -> None:
    rendered = _network_helpers()

    head = (
        f"INFLIGHT_REQUEST_LIMIT = {request_failures.INFLIGHT_REQUEST_LIMIT!r}\n"
        f"NETWORK_SETTLE_TIMEOUT_MS = {request_failures.NETWORK_SETTLE_TIMEOUT_MS!r}\n"
        f"NETWORK_QUIET_SECONDS = {request_failures.NETWORK_QUIET_SECONDS!r}\n"
        f"NETWORK_SETTLE_POLL_SECONDS = {request_failures.NETWORK_SETTLE_POLL_SECONDS!r}\n"
        f"STRICT_OPTIONS = {STRICT_OPTIONS!r}\n"
        f"ABORTED_REQUEST_FAILURES = frozenset({sorted(request_failures.ABORTED_REQUEST_FAILURES)!r})\n"
        f"HTTP_ERROR_RESOURCE_TYPES = frozenset({sorted(request_failures.HTTP_ERROR_RESOURCE_TYPES)!r})\n"
        f"LONG_LIVED_RESOURCE_TYPES = frozenset({sorted(request_failures.LONG_LIVED_RESOURCE_TYPES)!r})\n"
        "\n\n"
    )
    sources = [
        request_failures.is_http_error,
        request_failures.request_frame,
        request_failures.NetworkLedger,
        request_failures.settle_network,
        assertion_warning,
        strict_option,
        strict_refusal,
    ]

    assert rendered.startswith(head)
    assert rendered[len(head) :] == "\n\n\n".join(inspect.getsource(obj).rstrip() for obj in sources)
    assert STATE_HELPERS.startswith(rendered)
    compile(rendered, "<network helpers>", "exec")


@pytest.mark.parametrize("actions", [None, {"0": {"action": "nope"}}, "click", 3])
def test_a_macro_without_an_action_list_has_nothing_to_refuse(actions: object) -> None:
    assert refuse_unrunnable_steps("m", {"actions": actions}) is None


def test_every_unrunnable_step_is_named_in_the_refusal() -> None:
    macro = {"actions": [{"action": "click"}, {"action": "try"}, "junk", {"action": "launch"}]}

    with pytest.raises(ValueError) as excinfo:
        refuse_unrunnable_steps("Checkout", macro)

    assert str(excinfo.value) == (
        "macro 'Checkout' cannot be exported: the script cannot run step 1 (try), step 2 (None)"
    )


def test_indent_block_indents_text_lines_and_blanks_empty_ones() -> None:
    body = "\n\n  first\n\n   \nlastX\n\n"

    assert _indent_block(body, "    ") == "      first\n\n\n    lastX"
