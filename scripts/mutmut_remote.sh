#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Run `make mutmut` on another machine over SSH, then copy the score back.
#
# mutmut starts a pytest child per worker and copies the project per run; on a
# laptop that saturates CPU and disk for the length of the run. This sends the
# checkout to a host you can already `ssh` to, runs it there detached (an SSH
# drop or a sleeping laptop does not stop it), and fetches the results.
#
#   MUTMUT_REMOTE_HOST=<ssh host> make mutmut-remote [MUTMUT_JOBS=N]
#   MUTMUT_REMOTE_HOST=<ssh host> scripts/mutmut_remote.sh start|status|fetch|stop
#
# The host is read only from the environment and nothing in the repo names one.
# Use an alias from ~/.ssh/config so the address and key stay there as well.
#
# What is sent: the files git tracks (and new files already `git add`ed), with
# their working-tree content. Untracked and ignored files never leave this
# machine; the untracked ones are listed so a forgotten `git add` is visible.
# No git metadata is sent -- mutmut falls back to content hashes without it.
#
# The host needs bash, rsync, make and uv on PATH. Its first run installs the
# Python environment and Playwright's browsers (the libraries those browsers
# need are the host's business: `playwright install-deps` as an administrator).
#
# Environment:
#   MUTMUT_REMOTE_HOST   ssh destination (required)
#   MUTMUT_REMOTE_DIR    checkout on the host, relative to its home
#                        (default: octowright-mutmut)
#   MUTMUT_JOBS          mutmut workers on the host (default: its CPU count)
#   MUTMUT_REMOTE_FRESH  1 = discard the host's mutants/ (mutmut's incremental
#                        cache) before running
#   MUTMUT_REMOTE_POLL   seconds between progress lines (default: 30)
#
# Results land in .mutmut-remote/ (git-ignored): mutmut-cicd-stats.json (the
# score; see tests/AGENTS.md on why that file and not `mutmut results`), the
# run log and its exit status.

set -euo pipefail

HOST="${MUTMUT_REMOTE_HOST:-}"
DIR="${MUTMUT_REMOTE_DIR:-octowright-mutmut}"
POLL="${MUTMUT_REMOTE_POLL:-30}"
JOBS="${MUTMUT_JOBS:-}"
OUT=".mutmut-remote"
ACTION="${1:-run}"

die() {
    echo "mutmut-remote: $*" >&2
    exit 2
}

[ -n "$HOST" ] || die "set MUTMUT_REMOTE_HOST to an ssh host (an alias from ~/.ssh/config)"
case "$DIR" in
    "" | . | ./* | */. | */./* | /* | *..* | *[!A-Za-z0-9._/-]*)
        die "MUTMUT_REMOTE_DIR must be a plain relative path below the host's home" ;;
esac
case "$POLL" in
    "" | *[!0-9]*) die "MUTMUT_REMOTE_POLL must be a whole number of seconds" ;;
esac
case "$JOBS" in
    *[!0-9]*) die "MUTMUT_JOBS must be a whole number" ;;
esac

cd "$(git rev-parse --show-toplevel)"

remote() {
    # BatchMode: a missing key or an unknown host key fails instead of prompting.
    ssh -o BatchMode=yes "$HOST" "$@"
}

# Everything the host builds for itself, which a sync must never delete.
HOST_OWNED=(.venv/ mutants/ node_modules/ .mutmut-run/)

sync_tree() {
    local untracked
    untracked="$(git ls-files --others --exclude-standard)"
    if [ -n "$untracked" ]; then
        echo "mutmut-remote: not sending untracked files (git add them to include):" >&2
        sed 's/^/  /' <<<"$untracked" >&2
    fi
    # The prune below deletes every file git does not track, so it only ever
    # runs in a directory this script created: an existing one without the
    # marker (a typo naming a real checkout, say) is refused, not emptied.
    remote "if [ -d '$DIR' ] && [ -n \"\$(ls -A '$DIR')\" ] && [ ! -f '$DIR/.mutmut-run/checkout' ]; then
            echo 'mutmut-remote: ~/$DIR on the host exists and was not made by this script; refusing to sync into it' >&2; exit 3; fi
        mkdir -p '$DIR/.mutmut-run' && touch '$DIR/.mutmut-run/checkout'" || exit 3
    local protect=()
    for path in "${HOST_OWNED[@]}"; do
        protect+=("--filter=P /$path")
    done
    # --files-from takes names literally (no pattern matching), and a file git
    # stopped tracking is removed by the prune below, which --files-from cannot do.
    # --ignore-missing-args: a file deleted here but not yet committed is still
    # in the index, and would otherwise fail the whole transfer.
    git ls-files -z --cached | rsync -a --from0 --ignore-missing-args --files-from=- "${protect[@]}" ./ "$HOST:$DIR/"
    git ls-files -z --cached | remote "cd '$DIR' && python3 -c '
import os, sys
keep = {p for p in sys.stdin.buffer.read().split(b\"\\0\") if p}
owned = {os.fsencode(p.rstrip(\"/\")) for p in sys.argv[1:]}
for root, dirs, files in os.walk(b\".\"):
    rel_root = os.path.normpath(os.path.relpath(root, b\".\"))
    if rel_root == b\".\":
        dirs[:] = [d for d in dirs if d not in owned]
    for name in files:
        rel = os.path.normpath(os.path.join(rel_root, name))
        if rel not in keep:
            os.remove(rel)
' ${HOST_OWNED[*]}"
}

running() {
    remote "cd '$DIR' && test -f .mutmut-run/pid && kill -0 \$(cat .mutmut-run/pid) 2>/dev/null"
}

start() {
    if running; then
        die "a run is already going on the host; use: $0 status | stop"
    fi
    echo "mutmut-remote: preparing the host (first run installs Python deps and browsers)"
    remote "cd '$DIR' && mkdir -p .mutmut-run && rm -f .mutmut-run/status .mutmut-run/log &&
        { uv sync --all-groups --frozen && uv run playwright install chromium chromium-headless-shell firefox webkit; } \
            >.mutmut-run/setup.log 2>&1" ||
        die "host setup failed; its log is ~/$DIR/.mutmut-run/setup.log on the host"
    if [ "${MUTMUT_REMOTE_FRESH:-0}" = 1 ]; then
        remote "cd '$DIR' && rm -rf mutants"
    fi
    remote "cat > '$DIR/.mutmut-run/run.sh'" <<'RUNNER'
#!/usr/bin/env bash
# Written by scripts/mutmut_remote.sh; runs in its own session so it outlives ssh.
cd "$(dirname "$0")/.."
jobs="${1:-$(nproc)}"
make mutmut MUTMUT_JOBS="$jobs" >.mutmut-run/log 2>&1
rc=$?
uv run mutmut export-cicd-stats >>.mutmut-run/log 2>&1
echo "$rc" >.mutmut-run/status
RUNNER
    # cd first, on its own: `cd X && job &` backgrounds the cd with the job,
    # and the pid would be written from the home directory.
    remote "cd '$DIR' || exit 1
        setsid nohup bash .mutmut-run/run.sh $JOBS </dev/null >/dev/null 2>&1 &
        echo \$! >.mutmut-run/pid"
    echo "mutmut-remote: started (${JOBS:-all} workers); progress: $0 status"
}

status() {
    if remote "test -f '$DIR/.mutmut-run/status'"; then
        echo "finished, exit $(remote "cat '$DIR/.mutmut-run/status'")"
    elif running; then
        echo "running: $(remote "tail -c 400 '$DIR/.mutmut-run/log' 2>/dev/null" | tr '\r' '\n' | grep -v '^[[:space:]]*$' | tail -1)"
    else
        echo "not running"
        return 1
    fi
}

fetch() {
    # Cleared first: a run that wrote no score must not show the previous one's.
    rm -f "$OUT/log" "$OUT/status" "$OUT/mutmut-cicd-stats.json"
    mkdir -p "$OUT"
    rsync -a --ignore-missing-args \
        "$HOST:$DIR/.mutmut-run/log" "$HOST:$DIR/.mutmut-run/status" \
        "$HOST:$DIR/mutants/mutmut-cicd-stats.json" "$OUT/"
    if [ -f "$OUT/mutmut-cicd-stats.json" ]; then
        cat "$OUT/mutmut-cicd-stats.json"
        echo
    fi
    echo "mutmut-remote: results in $OUT/"
}

stop() {
    # The runner leads its own session, so its pid is its process group.
    remote "cd '$DIR' && test -f .mutmut-run/pid && kill -- -\$(cat .mutmut-run/pid) 2>/dev/null; rm -f .mutmut-run/pid"
    echo "mutmut-remote: stopped"
}

case "$ACTION" in
    run)
        sync_tree
        start
        while ! remote "test -f '$DIR/.mutmut-run/status'"; do
            sleep "$POLL"
            status || die "the run ended without an exit status (stopped?); see: $0 fetch"
        done
        fetch
        exit "$(cat "$OUT/status")"
        ;;
    start)
        sync_tree
        start
        ;;
    sync) sync_tree ;;
    status) status ;;
    fetch) fetch ;;
    stop) stop ;;
    *) die "unknown action '$ACTION' (run | start | status | fetch | stop | sync)" ;;
esac
