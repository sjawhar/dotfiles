#!/bin/bash
# Arm the 5-minute root-session hang watch (scripts/omp-root-hang-watch).
#
# Standalone installer (like session-cpu-record.sh) — not sourced by install.sh. Run it on the
# machine that runs the watched root session: the watch reads that machine's
# ~/.omp/agent/sessions, so there is nothing to arm anywhere else.
#
# Why this exists: from 2026-11-02 00:08Z to 01:59Z the SPEEDCOACH root session waited on a
# bash call that never returned, and two pull requests waited with it. Nothing told anybody.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "omp-root-hang-watch: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "omp-root-hang-watch: no envoy.json; the watch has no listener to send its alert to" >&2
    exit 1
}
# Respect a deliberate stop: a timer someone disabled after it was armed stays off.
if [[ -L "$HOME/.config/systemd/user/omp-root-hang-watch.timer" ]] \
    && ! systemctl --user is-enabled --quiet omp-root-hang-watch.timer 2>/dev/null; then
    echo "omp-root-hang-watch: timer is linked but disabled; leaving it off (enable it by hand to re-arm)"
    exit 0
fi

arm_user_timer omp omp-root-hang-watch
