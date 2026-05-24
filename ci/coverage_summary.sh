#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Emit a short coverage summary into $GITHUB_STEP_SUMMARY so PR reviewers see
# the totals at a glance. Reads from the .coverage data written by pytest-cov
# (whose configuration lives in pyproject.toml; we do not duplicate it here).
#
# Inputs:
#   GITHUB_STEP_SUMMARY  - written-to if set (otherwise stdout only)

set -euo pipefail

# Snapshot the total + per-file table from the existing .coverage data.
TOTAL_LINE="$(uv run --active coverage report --format=total 2>/dev/null || echo 'n/a')"

{
    echo "## Coverage"
    echo ""
    echo "**Total line coverage:** ${TOTAL_LINE}%"
    echo ""
    echo "<details><summary>Per-file report</summary>"
    echo ""
    echo '```'
    uv run --active coverage report --skip-covered --sort=cover || true
    echo '```'
    echo ""
    echo "</details>"
} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
