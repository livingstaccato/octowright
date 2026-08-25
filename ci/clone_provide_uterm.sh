#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Put a provide-uterm checkout where uv expects it, so the terminal session-kind
# plugin (packages/octowright-terminal) can actually be installed in CI.
#
# Why a clone and not `pip install provide-uterm`: the provide-uterm packages are
# UNPUBLISHED -- PyPI 404s them -- so octowright-terminal is source-install-only
# (see packages/octowright-terminal/README.md). The repo itself is PUBLIC, so a
# clone needs no token, which is the difference between running the plugin's test
# suite for real and leaving it silently skipped. It skipped for the whole life of
# the old octowright[terminal] extra, and that is how three real failures went
# unnoticed until the extraction forced someone to install uterm locally.
#
# WHERE: [tool.uv.sources] in the root pyproject.toml resolves the four uterm
# packages at ../provide-uterm relative to the octowright checkout root, so the
# destination is this repo's PARENT directory. On a GitHub runner that is
# /home/runner/work/octowright/provide-uterm -- outside $GITHUB_WORKSPACE, but
# writable, which is why this is a plain `git clone` rather than an
# actions/checkout with `path:` (that action refuses paths outside the workspace,
# and moving both repos into subdirectories would change every job's working
# directory).
#
# PINNED, deliberately: an unpinned clone would make octowright's CI go red for
# upstream changes nobody here made, which trains people to ignore CI. The pin is
# what makes this job fair to block on.
#
# TO BUMP THE PIN: pick a commit from https://github.com/provide-io/provide-uterm
# (`gh api repos/provide-io/provide-uterm/commits/main --jq .sha`), replace
# PROVIDE_UTERM_REF below, and re-run CI. Override per-run with the env var of
# the same name for a one-off check against a newer upstream.

set -euo pipefail

# provide-uterm main @ 2026-08-25 ("docs(changelog): record the last four fixes
# under 0.5.1"). The plugin's suite was verified green against exactly this
# commit before it was pinned here.
PROVIDE_UTERM_REF="${PROVIDE_UTERM_REF:-73d458cc2f015a272c3bdc80813eb5dd5af07bd7}"
PROVIDE_UTERM_REPO="${PROVIDE_UTERM_REPO:-https://github.com/provide-io/provide-uterm.git}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="$(dirname "$repo_root")/provide-uterm"

# An EXISTING checkout is left strictly alone unless PROVIDE_UTERM_FORCE=1.
# On a runner the destination never pre-exists, so the clone branch is the only
# one CI takes -- but in the provide.io dev layout ../provide-uterm is the
# developer's own working checkout (often a symlink to it), and a script that
# fetched and detached its HEAD because someone ran a ci/ script by hand would be
# a nasty surprise. Report and warn instead; do not touch it.
# `-e` dereferences, so a BROKEN symlink at $dest is false to it and control
# falls through to `git clone`, which then refuses with "could not create work
# tree dir ... File exists" -- a safe failure with a misleading message, since
# nothing was created at the dangling target. `-L` names it for what it is.
if [[ -e "$dest" || -L "$dest" ]]; then
    if [[ "${PROVIDE_UTERM_FORCE:-0}" != "1" ]]; then
        current="$(git -C "$dest" rev-parse HEAD 2>/dev/null || echo "unknown")"
        echo "provide-uterm already present at $dest (HEAD $current) — leaving it untouched"
        if [[ "$current" != "$PROVIDE_UTERM_REF" ]]; then
            echo "NOTE: that is not the pinned commit $PROVIDE_UTERM_REF." >&2
            echo "      Set PROVIDE_UTERM_FORCE=1 to fetch and detach it to the pin." >&2
        fi
        exit 0
    fi
    echo "PROVIDE_UTERM_FORCE=1 — fetching and re-pinning $dest"
    git -C "$dest" fetch --quiet origin
else
    git clone --quiet "$PROVIDE_UTERM_REPO" "$dest"
fi

# --detach: this checkout is a fixed input, never a branch anyone commits to.
git -C "$dest" checkout --quiet --detach "$PROVIDE_UTERM_REF"

echo "provide-uterm at $dest -> $(git -C "$dest" rev-parse HEAD)"
