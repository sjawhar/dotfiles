#!/bin/bash
# Arm the 2-minute CPU and swap watch (scripts/load-swap-watch).
#
# Standalone installer (like disk-fill-watch.sh and omp-memory-watch.sh) — not sourced by
# install.sh. Run it on a machine that carries agent sessions: the watch reads THIS machine's
# /proc to decide whether its agents are being starved of CPU or its swap is gone, so there is
# nothing to arm on a laptop that only SSHes in.
#
# Why this exists: on 2026-10-29 six agent lanes on ossv-agent-open were reported frozen for
# over an hour. The box was not hung — it carried 126 omp processes on 32 cores with
# `cpu some avg60` at 80% and a 192 GiB swapfile 99.75% full, so every lane was being served a
# fraction of a core with its working set in swap. No instrument on the box watched either
# number, and nothing alerted. scripts/load-swap-watch.md has the measurements.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "load-swap-watch: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "load-swap-watch: no envoy.json; the watch has no listener to send its alerts through" >&2
    exit 1
}
if ! [ -r /proc/pressure/cpu ]; then
    echo "NOTE: this kernel has no PSI (/proc/pressure/cpu); the watch falls back to the run"
    echo "      queue per core, which says how many tasks want a CPU but not how long they waited."
fi
if [ "$(awk '/^SwapTotal:/{print $2}' /proc/meminfo)" = "0" ]; then
    echo "NOTE: this machine has no swap, so only the CPU and MemAvailable rules can fire."
fi
mkdir -p /mnt/nvme/sami/tmp/load-swap-watch 2>/dev/null \
    || echo "NOTE: /mnt/nvme/sami/tmp is absent here; the watch keeps its state under \$XDG_STATE_HOME."

arm_user_timer omp load-swap-watch
