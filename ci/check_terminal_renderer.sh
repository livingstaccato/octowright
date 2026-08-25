#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Prove that packages/octowright-terminal/src/octowright_terminal/assets/renderer.js
# is what packages/octowright-terminal/assets-src/src/renderer.ts currently builds.
#
# That file is a COMMITTED ~321 KB build artifact, on purpose: a Python wheel has
# no npm step at install time, and the dashboard serves plugin assets verbatim
# off disk (http/routes/plugin_assets.py), so the bundle -- xterm and two addons
# inlined -- has to be in the distribution. The cost of that choice is that
# editing renderer.ts and forgetting to rebuild ships a stale renderer with no
# signal at all. This step is that signal.
#
# The install runs INSIDE assets-src on purpose. That directory has its own
# package.json/package-lock.json and is deliberately NOT a member of the root npm
# workspace (root `workspaces` lists only packages/octowright-frontend), because
# that separation is what keeps xterm out of core's own bundle -- the whole
# measurable payoff of moving terminal into a plugin (317.60 kB / 78.62 kB gzip
# gone from core's frontend). Do not "simplify" this by adding assets-src to the
# root workspaces: that would undo it.
#
# The comparison is a byte compare against a copy taken before the build, NOT
# `git diff`. Two reasons: `git diff --exit-code` reports 1 for "differs" and for
# "git failed" alike, so a git-level problem would be announced as a stale
# artifact; and under nektos/act there is no .git in the workspace at all, which
# made exactly that happen ("fatal: not a git repository" reported as STALE).
# The saved copy is also restored on failure, so a red run leaves the tree as it
# found it.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
assets_src="$repo_root/packages/octowright-terminal/assets-src"
artifact="$repo_root/packages/octowright-terminal/src/octowright_terminal/assets/renderer.js"

if [[ ! -f "$artifact" ]]; then
    echo "FAIL: $artifact does not exist; the committed renderer bundle is missing" >&2
    exit 1
fi

committed="$(mktemp)"
trap 'rm -f "$committed"' EXIT
cp "$artifact" "$committed"

npm ci --prefix "$assets_src"
npm run build --prefix "$assets_src"

if ! cmp -s "$committed" "$artifact"; then
    cp "$committed" "$artifact"
    cat >&2 <<MSG

FAIL: packages/octowright-terminal/src/octowright_terminal/assets/renderer.js is
stale — it does not match a fresh build of
packages/octowright-terminal/assets-src/src/renderer.ts.

Rebuild and commit the artifact alongside the source change:

    cd packages/octowright-terminal/assets-src
    npm ci
    npm run build
    git add ../src/octowright_terminal/assets/renderer.js
MSG
    exit 1
fi

echo "renderer.js matches a fresh build of its source"
