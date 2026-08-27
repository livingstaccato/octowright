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

# mutmut 3.x `results` prints no totals line -- it prints one
# "    <mutant>: <status>" line per mutant that was NOT killed (killed ones are
# omitted unless --all). Count per status. An earlier version of this script
# grepped for a "survived: N" line that mutmut never emits, and reported
# "(no count reported)" on the first run that ever reached it.
count_status() {
    printf '%s\n' "${RESULTS}" | grep -cE ": ${1}\$" || true
}
SURVIVED="$(count_status survived)"
NO_TESTS="$(count_status 'no tests')"
TIMEOUT="$(count_status timeout)"
SUSPICIOUS="$(count_status suspicious)"

{
    echo "## Mutation testing (mutmut)"
    echo ""
    echo "**survived: ${SURVIVED}** · no tests: ${NO_TESTS} · timeout: ${TIMEOUT} · suspicious: ${SUSPICIOUS}"
    echo ""
    echo "<details><summary>Full results</summary>"
    echo ""
    echo '```'
    printf '%s\n' "${RESULTS}"
    echo '```'
    echo ""
    echo "</details>"
} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
