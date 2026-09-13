#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Smoke-test the `octowright skill` CLI: dry-run install, real install,
# status, and doctor. CODEX_HOME must be set by the caller so the install
# is sandboxed inside the CI workspace.

set -euo pipefail

uv run octowright skill install octowright --target all --dry-run
uv run octowright skill install octowright --target all
uv run octowright skill status octowright --target all --json
uv run octowright skill doctor --json
