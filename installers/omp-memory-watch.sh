#!/bin/bash
# Arm the 30-second omp memory watch (scripts/omp-memory-watch).
#
# Standalone installer (like envoy-role-holder-watch.sh) — not sourced by install.sh.
# Run it on a machine that runs omp sessions: the watch reads THIS machine's /proc and
# /sys/fs/cgroup to decide whether a session's systemd scope is heading for its MemoryMax, so
# there is nothing to arm on a laptop that only SSHes in.
#
# Why this exists: on 2026-10-27T05:55:14Z the kernel OOM-killed an agent session inside its
# 24 GiB scope with no warning, and about 40 of its subagent lanes died with it.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "omp-memory-watch: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "omp-memory-watch: no envoy.json; the watch has no listener to send its alerts through" >&2
    exit 1
}
[ -r /sys/fs/cgroup/cgroup.controllers ] || {
    echo "omp-memory-watch: no cgroup v2 at /sys/fs/cgroup; the watch reads memory.max there" >&2
    exit 1
}
mkdir -p /mnt/nvme/sami/tmp/omp-memory-watch 2>/dev/null \
    || echo "NOTE: /mnt/nvme/sami/tmp is absent here; pass --state to put the watch's state elsewhere."

arm_user_timer omp omp-memory-watch
