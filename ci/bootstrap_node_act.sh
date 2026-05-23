#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Under nektos/act, the bundled node binary lives under /opt/acttoolcache
# and is not on PATH for post-action steps. Symlink the newest version into
# /usr/local/bin/node so downstream `node`-dependent actions work. No-op on
# real GitHub runners (ACT is unset there); the bootstrap-uv action only
# invokes this script when ACT=true.

set -x

which node || true
ls -la /opt/acttoolcache/node/*/x64/bin/node 2>/dev/null || true
n=$(ls -t /opt/acttoolcache/node/*/x64/bin/node 2>/dev/null | head -1 || true)
echo "Found node: $n"
if [ -n "$n" ] && [ ! -x /usr/local/bin/node ]; then
    ln -sf "$n" /usr/local/bin/node
    echo "Symlinked $n -> /usr/local/bin/node"
fi
which node
ls -la /usr/local/bin/node || true
