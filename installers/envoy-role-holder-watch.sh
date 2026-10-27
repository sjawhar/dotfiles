#!/bin/bash
# Arm the 30-second Envoy role-holder watch (scripts/envoy-role-holder-watch).
#
# Standalone installer (like omp-billing-watch.sh) — not sourced by install.sh.
# Run it on a machine that runs omp sessions holding Envoy roles: the watch reads THIS
# machine's /proc to decide whether a role's holder still has a process, so there is nothing
# to arm on a laptop that only SSHes in.
#
# Why this exists: a role claim outlives its holder's process. When the holder dies, messages
# to notifications.role.<role> keep looking deliverable and reach nobody.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "envoy-role-holder-watch: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "envoy-role-holder-watch: no envoy.json; the watch has no listener to read roles from" >&2
    exit 1
}
mkdir -p /mnt/nvme/sami/tmp/envoy-role-holder-watch 2>/dev/null \
    || echo "NOTE: /mnt/nvme/sami/tmp is absent here; pass --state to put the watch's state elsewhere."

arm_user_timer omp envoy-role-holder-watch
