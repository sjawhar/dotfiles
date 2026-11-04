#!/bin/bash
# Arm the 5-minute runner of the streams' standing jobs (scripts/stream-job, stream-job.md).
#
# Standalone installer, not sourced by install.sh. Run it on the host that holds the job files
# in ~/.config/stream-jobs; the runner reads that machine's own files, so there is nothing to
# arm anywhere else.
#
# The timer is also in devbox/expected-timers.txt, so installers/expected-timers.sh arms it
# with the rest and unit-health-watch says so if it goes missing.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "stream-job: python3 not on PATH; the runner is a python3 script" >&2
    exit 1
}
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/stream-jobs"
mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}/stream-jobs"
# Respect a deliberate stop: a timer someone disabled after it was armed stays off.
if [[ -L "$HOME/.config/systemd/user/stream-job.timer" ]] \
    && ! systemctl --user is-enabled --quiet stream-job.timer 2>/dev/null; then
    echo "stream-job: timer is linked but disabled; leaving it off (enable it by hand to re-arm)"
    exit 0
fi

arm_user_timer devbox stream-job
