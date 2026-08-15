#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Three-phase pytest run used by the GitHub Actions test matrix:
#   1. integration_local subset → writes junitxml so artefacts upload
#   2. main suite (everything except integration_local and memory_isolated)
#   3. memory_isolated subset, alone in its own process
#
# Why phase 3 is separate: memory_isolated tests assert on a process-wide
# tracemalloc heap diff. Interleaved with the ~5000 other tests in phase 2,
# unrelated tests' retained state (import caches, log-capture buffers) pollutes
# the measurement window — observed on CI as a consistent 700KB-2MB "growth"
# against a 500KB band, on every platform, while the same test run alone always
# passed at the ~20KB the band was calibrated against. Running it in its own
# process removes that contamination without touching the assertion itself.
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
    uv run --active pytest -vv tests/ -m "not integration_local and not memory_isolated" --tb=short -ra --color=no
else
    uv run --active pytest -q tests/ -m "not integration_local and not memory_isolated"
fi

uv run --active pytest -q tests/ -m memory_isolated --no-cov
