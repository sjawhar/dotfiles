#!/bin/bash
# Arm the 5-minute per-session CPU recorder (scripts/session-cpu-record).
#
# Standalone installer (like load-swap-watch.sh and disk-fill-watch.sh) — not sourced by
# install.sh. Run it on a machine that carries agent sessions: it records THIS machine's
# sessions from its own /proc and cgroups.
#
# Why this exists: on 2026-11-01 a speed tick asked which omp sessions had used the box's 64
# cores and the answer was "not measurable" — sysstat here runs with SADC_OPTIONS="-S DISK",
# there is no atop, and load-swap-watch names consumers only inside an alert. The question can
# only be answered while the processes live, so something has to write it down every few
# minutes. scripts/session-cpu-record.md says what is recorded and how to read it back.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "session-cpu-record: python3 not on PATH; the recorder is a python3 script" >&2
    exit 1
}
[ -r /proc/1/cgroup ] || {
    echo "session-cpu-record: no /proc/<pid>/cgroup here; there is nothing to sample" >&2
    exit 1
}
if ! [ -r /sys/fs/cgroup/cgroup.controllers ]; then
    echo "NOTE: no cgroup v2 at /sys/fs/cgroup; every session is counted from /proc/<pid>/stat,"
    echo "      which misses a child that has not been reaped yet."
fi
records=/mnt/nvme/sami/tmp/session-cpu
if mkdir -p "$records" 2>/dev/null; then
    echo "session-cpu-record: records go to $records (one file per UTC day, 7 days kept)"
else
    echo "NOTE: /mnt/nvme/sami/tmp is absent here; the records go under \$XDG_STATE_HOME instead."
fi

arm_user_timer omp session-cpu-record
