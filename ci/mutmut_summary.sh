#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Parse `mutmut results` output and emit a one-line summary (plus the full
# breakdown) into $GITHUB_STEP_SUMMARY. Non-fatal — mutmut already ran and
# this script must never fail the job.
#
# Inputs:
#   GITHUB_STEP_SUMMARY  - written-to if set (otherwise stdout only)

set -uo pipefail

RESULTS="$(PYTHONPATH=src uv run --active mutmut results 2>&1 || true)"

# Surviving mutants live under the "survived" status; mutmut 3.x prints a
# line like "survived: 12" in its summary tail. Grep tolerantly.
SURVIVED="$(printf '%s\n' "${RESULTS}" | grep -iE '^[[:space:]]*survived' | head -n1 || true)"
[[ -z "${SURVIVED}" ]] && SURVIVED="survived: (no count reported)"

{
    echo "## Mutation testing (mutmut)"
    echo ""
    echo "**${SURVIVED}**"
    echo ""
    echo "<details><summary>Full results</summary>"
    echo ""
    echo '```'
    printf '%s\n' "${RESULTS}"
    echo '```'
    echo ""
    echo "</details>"
} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
