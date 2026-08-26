#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Run the terminal session-kind plugin's own suite (packages/octowright-terminal),
# which core's `pytest tests/` legs deliberately do not reach.
#
# Requires the `terminal` dependency group synced, which is the terminal-plugin
# job's bootstrap step (every other job excludes that group on purpose).
#
# The availability guard below is the point of this script. The plugin's
# conftest.py sets `collect_ignore_glob` when uterm is missing, because these
# tests import uterm-backed modules and would otherwise error at COLLECTION
# rather than skip. That is correct for a core install and catastrophic for a CI
# gate: pytest reports a clean pass over zero tests, and a green check means
# nothing. So assert availability FIRST and fail loudly, rather than letting the
# suite quietly collect nothing.

set -euo pipefail

uv run --active python - <<'PY'
import sys

FIX = (
    "Sync the `terminal` dependency group first: "
    "uv sync --frozen --all-groups."
)

try:
    from octowright_terminal.availability import is_available
except ImportError as exc:
    sys.exit(f"octowright-terminal is not installed ({exc}), so there is nothing to test. {FIX}")

if not is_available():
    sys.exit(
        "provide-uterm is not importable, so the terminal plugin's conftest would "
        f"ignore every test in the suite and this job would pass over zero tests. {FIX}"
    )
print("provide-uterm available — the terminal suite will actually collect")
PY

# --no-cov: the root addopts measure coverage of src/octowright and the report
# threshold in pyproject.toml is calibrated against core's full suite. Measuring
# it from this suite alone would fail on a number that means nothing here.
uv run --active pytest -q packages/octowright-terminal/tests --no-cov

# End-to-end proof that the entry point reaches the real daemon tool surface, not
# just importlib.metadata: enabling the plugin by name must add exactly the seven
# terminal_* tools, and leaving it disabled must add none. Asserted as a DELTA
# rather than against a hardcoded 129/136 so that adding a browser tool tomorrow
# does not fail this job for an unrelated reason.
core_tools="$(uv run --active octowright selftest | grep -c '^  - ')"
core_terminal_tools="$(uv run --active octowright selftest | grep -c '^  - terminal_' || true)"
plugin_tools="$(OCTOWRIGHT_PLUGINS=terminal uv run --active octowright selftest | grep -c '^  - ')"
plugin_terminal_tools="$(OCTOWRIGHT_PLUGINS=terminal uv run --active octowright selftest | grep -c '^  - terminal_' || true)"

echo "tool surface: core=${core_tools} (terminal_*=${core_terminal_tools}), OCTOWRIGHT_PLUGINS=terminal -> ${plugin_tools} (terminal_*=${plugin_terminal_tools})"

if [[ "$core_terminal_tools" -ne 0 ]]; then
    echo "FAIL: an unconfigured daemon registered ${core_terminal_tools} terminal_* tools; plugins must load only when enabled by name" >&2
    exit 1
fi
if [[ "$plugin_terminal_tools" -ne 7 ]]; then
    echo "FAIL: expected 7 terminal_* tools with OCTOWRIGHT_PLUGINS=terminal, got ${plugin_terminal_tools}" >&2
    exit 1
fi
if [[ $((plugin_tools - core_tools)) -ne 7 ]]; then
    echo "FAIL: enabling the terminal plugin changed the tool count by $((plugin_tools - core_tools)), expected exactly 7" >&2
    exit 1
fi
echo "terminal plugin tool surface OK"
