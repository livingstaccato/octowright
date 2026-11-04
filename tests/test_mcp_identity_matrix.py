# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The per-client reconnect matrix is one structure read by two surfaces.

``INSTRUCTIONS`` is what a disconnected client's agent tells the user;
``octowright doctor``'s followers check prints the same step for the client
it names. Both render from ``RECONNECT_STEPS`` — these pin the rendering so
the matrix can only change deliberately, and the no-session clients keep
their grouped sentence (adding a client to the wrong bucket would silently
demote an in-session reconnect to a restart).
"""

from __future__ import annotations

from octowright import mcp_identity


def test_instructions_render_the_matrix_verbatim() -> None:
    """INSTRUCTIONS carries the matrix as-is: an entry added to RECONNECT_STEPS
    without rendering would be advice the operator sees and the client never
    hears (the drift this refactor exists to prevent)."""
    matrix = " ".join(mcp_identity.reconnect_matrix_lines())
    for entry in mcp_identity.RECONNECT_STEPS:
        if entry.in_instructions and entry.step:
            assert f"{entry.name} — {entry.step}." in matrix
    assert matrix in mcp_identity.INSTRUCTIONS


def test_no_session_clients_are_named_as_one_group() -> None:
    text = " ".join(mcp_identity.reconnect_matrix_lines())
    assert "Codex CLI, OpenCode, and Amp have NO in-session reconnect" in text
    assert text.index("have NO in-session") > text.index("Continue or Zed"), (
        "the no-session group sentence comes after the in-session steps"
    )


def test_pi_and_omp_have_in_session_steps() -> None:
    names = {entry.name for entry in mcp_identity.RECONNECT_STEPS if entry.in_instructions}
    assert {"pi", "omp"} <= names
    for entry in mcp_identity.RECONNECT_STEPS:
        if entry.name in {"pi", "omp"}:
            assert entry.step == "/mcp reconnect octowright (or /mcp reload)"


def test_client_step_falls_back_to_restart_for_no_session_clients() -> None:
    codex = next(entry for entry in mcp_identity.RECONNECT_STEPS if not entry.step)
    text = mcp_identity.client_step(codex)
    assert "restart the client" in text
    assert "loses the current conversation" in text


def test_vs_code_is_detection_only() -> None:
    """The extension host is one binary for Cline and Copilot; the server text
    lists the two real steps instead of guessing."""
    vs_code = next(entry for entry in mcp_identity.RECONNECT_STEPS if entry.name == "VS Code")
    assert vs_code.in_instructions is False
    assert "Cline:" in vs_code.step and "Copilot:" in vs_code.step
