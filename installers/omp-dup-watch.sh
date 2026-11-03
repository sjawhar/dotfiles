#!/bin/bash
# Arm the 5-minute duplicate-session watch (scripts/omp-dup-watch).
#
# Standalone installer (like omp-memory-watch.sh and load-swap-watch.sh) — not sourced by
# install.sh. Run it on a machine that carries agent sessions: the watch reads THIS machine's
# /proc for every live omp, its own agent boxes through `docker ps`, and Envoy's registrations
# for this host, so there is nothing to arm on a laptop that only SSHes in.
#
# Why this exists: on 2026-11-03, 00:29:44-01:08:51Z, a second tmux server on sami-rocqperf
# restored the saved layout and resumed every saved session. 36 sessions got a second omp in
# that server's panes and 14 got one in a new agent box each. Two omps on one session write one
# transcript and Envoy reaches only one of them; nothing on the box watched for it, and a
# person found it 17 minutes later. scripts/omp-dup-watch.md has the measurements.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "omp-dup-watch: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "omp-dup-watch: no envoy.json; the watch has no listener to send its alerts through" >&2
    exit 1
}
docker ps >/dev/null 2>&1 \
    || echo "NOTE: \`docker ps\` does not answer here, so an omp in an agent box is named by its
      container id instead of its box name. The duplicate itself is still found: a box's omp is
      visible in the host's /proc."
mkdir -p /mnt/nvme/sami/tmp/omp-dup-watch 2>/dev/null \
    || echo "NOTE: /mnt/nvme/sami/tmp is absent here; the watch keeps its state and its reports
      under \$XDG_STATE_HOME."

ensure_box_health_owner omp-dup-watch || exit 1
arm_user_timer omp omp-dup-watch
