#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Two-phase pytest run used by the GitHub Actions test matrix:
#   1. integration_local subset → writes junitxml so artefacts upload
#   2. main suite (everything except integration_local)
#
# Why a script: keeps the YAML to a one-line `run:` so Windows' default
# PowerShell doesn't choke on bash-style `\` line continuations (see
# CLAUDE.md "CI / GitHub Actions" policy).
#
# Required env:
#   RUNNER_OS    — passed through from the workflow (e.g. Linux, macOS, Windows)
#   RUNNER_ARCH  — e.g. amd64, arm64
# Optional env:
#   VERBOSE=1    — Windows leg passes this so the main-suite run is `-vv` with
#                  --tb=short -ra --color=no; everything else stays terse.

set -euo pipefail

OS="${RUNNER_OS:-unknown}"
ARCH="${RUNNER_ARCH:-unknown}"
JUNIT_PATH=".ci/integration-local-${OS}-${ARCH}.xml"

mkdir -p .ci

uv run --active pytest -q tests/ -m integration_local --no-cov \
    --junitxml="$JUNIT_PATH"

if [[ "${VERBOSE:-0}" == "1" ]]; then
    uv run --active pytest -vv tests/ -m "not integration_local" --tb=short -ra --color=no
else
    uv run --active pytest -q tests/ -m "not integration_local"
fi
