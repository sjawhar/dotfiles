#!/bin/bash
# Arm the 5-minute disk fill watch (scripts/disk-fill-watch).
#
# Standalone installer (like omp-memory-watch.sh) — not sourced by install.sh. Run it on a
# machine whose disks agent sessions fill: the watch reads THIS machine's statvfs and /proc to
# decide whether a filesystem is heading for full, so there is nothing to arm on a laptop that
# only SSHes in.
#
# Why this exists: on 2026-10-27T00:00:31Z the root filesystem of sami-rocqperf reached zero
# bytes free for 11 seconds; rsyslog, journald and dockerd's exec all failed with ENOSPC and
# nothing had alerted. The only instrument was a watch loop inside an agent session, which died
# with that session at 09:18Z. (dispatch://INFRA-613)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "disk-fill-watch: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "disk-fill-watch: no envoy.json; the watch has no listener to send its alerts through" >&2
    exit 1
}
mkdir -p /mnt/nvme/sami/tmp/disk-fill-watch 2>/dev/null \
    || echo "NOTE: /mnt/nvme/sami/tmp is absent here; the watch keeps its state under \$XDG_STATE_HOME."

# The writer attribution in an alert reads /proc/<pid>/io of every process, which needs root for
# another user's. Without passwordless sudo the watch still runs and still alerts; its alert then
# says how many processes refused the read, so root's own writers are never reported as absent.
if ! sudo -n true 2>/dev/null; then
    echo "NOTE: sudo -n is not allowed here, so an alert names only this user's writers."
fi

ensure_box_health_owner disk-fill-watch || exit 1
arm_user_timer omp disk-fill-watch
