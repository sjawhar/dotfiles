#!/bin/bash
# Arm the hourly ~/src symlink watch (scripts/src-symlink-watch).
#
# Standalone installer (like envoy-role-holder-watch.sh) — not sourced by install.sh. Run it on a
# machine that runs agent boxes: it reads THIS machine's ~/src and counts THIS machine's live
# boxes, so there is nothing to arm on a laptop that only SSHes in.
#
# Why this exists: agentbox gives every box ~/src together with the targets of the symlinks
# inside it, so one link added to ~/src silently mounts its target into every box on the machine
# — 82 of them on sami-devbox on 2026-11-07, when five links were found pulling five lanes'
# private worktrees and clones into all of them. Each had been there for up to twelve days, none
# was shared infrastructure, and nothing had said so. (dispatch://INFRA-675)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "src-symlink-watch: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "src-symlink-watch: no envoy.json; the watch has no listener to send its notice through" >&2
    exit 1
}
[ -d "$HOME/src" ] || {
    echo "src-symlink-watch: no ~/src on this machine; there is nothing to watch" >&2
    exit 1
}

arm_user_timer omp src-symlink-watch
