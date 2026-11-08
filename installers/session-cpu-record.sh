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
# The same path scripts/session-cpu-record picks: the big disk's directory is named for the home
# it belongs to, so the installer carries no one machine's path.
nvme_tmp="/mnt/nvme/$(basename "$HOME")/tmp"
records="$nvme_tmp/session-cpu"
if mkdir -p "$records" 2>/dev/null; then
    echo "session-cpu-record: records go to $records (one file per UTC day, 7 days kept)"
else
    echo "NOTE: $nvme_tmp is absent here; the records go under \$XDG_STATE_HOME instead."
fi

# Since 2026-11-04 this is a stream job where the box has the runner, not a timer of its own:
# sami-devbox's 23 user timers were folded into about four
# (dispatch://INFRA-993/ask/d44b73ba), and a 5-minute recorder with no alert of its own is
# exactly what scripts/stream-job exists to run. The runner's own timer fires it, and
# unit-health-watch reads the same state and names the owner below when it stops finishing.
#
# Not every machine has the runner: sami-rocqperf had session-cpu-record.timer enabled and no
# stream-job.timer at all on 2026-11-04, and writing a job file there would have left it with no
# recorder. So the runner decides which of the two this machine gets, and a machine never ends
# up with both.
jobs="${XDG_CONFIG_HOME:-$HOME/.config}/stream-jobs"
if systemctl --user is-enabled --quiet stream-job.timer 2>/dev/null; then
    mkdir -p "$jobs"
    cat >"$jobs/session-cpu-record.job" <<'JOB'
# Per-session CPU on this box, sampled so sessions can be ranked afterwards. Owner: the
# box-health lead, who is who unit-health-watch names when this job stops finishing. timeout is
# the old unit's TimeoutStartSec: one sample cost ~0.5 CPU-seconds and ~18 s of wall clock at
# load 300, and a run that cannot finish is killed so runs never pile up on the 5-minute period.
owner=01a18385-f8c8-74a9-b6ff-694fb5fe87a7
command=$HOME/.dotfiles/scripts/session-cpu-record sample
every=5m
timeout=4m
JOB
    echo "session-cpu-record: $jobs/session-cpu-record.job written; scripts/stream-job runs it"
    if systemctl --user is-enabled --quiet session-cpu-record.timer 2>/dev/null; then
        systemctl --user disable --now session-cpu-record.timer
        echo "session-cpu-record: the old timer was armed here too and has been disabled"
    fi
else
    echo "session-cpu-record: this machine has no stream-job.timer, so it keeps its own timer."
    rm -f "$jobs/session-cpu-record.job"
    arm_user_timer omp session-cpu-record
fi
