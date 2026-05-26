#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Run the pytest suite under nektos/act with the act-unsafe tests skipped.
# Used by the CI test job when ACT=true.

set -euo pipefail

uv run --active pytest -q tests/ \
    --ignore=tests/test_engine_matrix_live.py \
    --ignore=tests/test_badge.py \
    --ignore=tests/test_pill.py \
    --ignore=tests/test_popup_listeners.py \
    --ignore=tests/test_scenario_sync.py \
    --ignore=tests/test_scenarios_live.py \
    --ignore=tests/test_session_mode.py \
    --ignore=tests/test_title_prefix.py \
    --ignore=tests/test_label_promotion.py
