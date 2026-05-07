#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Run detect-secrets-hook over every tracked file except the baseline itself
# and the lockfile (lockfiles produce noisy false positives that don't carry
# real secrets). Used by both `make lint` and the CI lint job.

set -eu

# Portable across macOS bash 3.2 and Linux bash 4+. detect-secrets-hook needs
# the file list as positional args; xargs with a NUL delimiter handles paths
# with spaces or unusual characters.
git ls-files -z \
    | grep -zEv '^(\.secrets\.baseline|uv\.lock)$' \
    | xargs -0 uv run --active detect-secrets-hook --baseline .secrets.baseline
