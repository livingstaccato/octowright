#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Run the pytest suite under nektos/act with the act-unsafe tests skipped.
# Used by the CI test job when ACT=true.

set -euo pipefail

# memory_isolated tests assert on a process-wide tracemalloc heap diff and must
# run alone, not interleaved with the rest of the suite — see
# ci/run_integration_and_main.sh for the full explanation.
uv run --active pytest -q tests/ \
    -m "not memory_isolated" \
    --ignore=tests/test_engine_matrix_live.py \
    --ignore=tests/test_badge.py \
    --ignore=tests/test_pill.py \
    --ignore=tests/test_popup_listeners.py \
    --ignore=tests/test_scenario_sync.py \
    --ignore=tests/test_scenarios_live.py \
    --ignore=tests/test_session_mode.py \
    --ignore=tests/test_title_prefix.py \
    --ignore=tests/test_label_promotion.py

uv run --active pytest -q tests/ -m memory_isolated --no-cov
