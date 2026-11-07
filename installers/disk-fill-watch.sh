#!/bin/bash
# Arm the 5-minute disk fill watch (scripts/disk-fill-watch).
#
# Standalone installer — not sourced by install.sh. Run it on a
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

# sami-devbox (dispatch://INFRA-993, "Software") has four disks, each with its own floor: boot
# 50 GiB, state (/mnt/nvme) 400, local-SSD scratch 500, cold (/mnt/durable) 200. Its state disk
# is capped at 160,000 IOPS and its cold disk at 3,000, not the Azure P50's 7,500 the script
# assumes for /mnt/durable. The layout, not the host name, picks it: a machine whose
# /mnt/scratch and /mnt/durable are both mounts is that machine. The unit is committed and
# linked, so the per-machine arguments go in a drop-in beside the link.
# --no-mount-alerts is this machine only: agent-infra#2349 (merged 2026-11-07 15:15:09Z)
# gives sami-devbox the owner:sjawhar attribute, which puts it inside the Datadog host
# monitors that already page on a filesystem filling. The timer stays for its other three
# duties -- the IOPS-cap check, naming the processes that are writing, and the free-space
# series the forecast reads -- but two alerts for one crossing teach people to ignore both.
# A machine outside those monitors keeps the alert, which is why the flag is here and not
# in the committed unit.
dropin="$HOME/.config/systemd/user/disk-fill-watch.service.d/devbox-disks.conf"
if mountpoint -q /mnt/scratch && mountpoint -q /mnt/durable; then
    mkdir -p "$(dirname "$dropin")"
    printf '%s\n' '[Service]' 'ExecStart=' \
        'ExecStart=%h/.dotfiles/scripts/disk-fill-watch --mount / --mount /mnt/nvme --mount /mnt/scratch --mount /mnt/durable --floor /=50 --floor /mnt/nvme=400 --floor /mnt/scratch=500 --floor /mnt/durable=200 --iops-cap /mnt/nvme=160000 --iops-cap /mnt/durable=3000 --no-mount-alerts' \
        >"$dropin.new"
    if cmp -s "$dropin.new" "$dropin"; then rm -f "$dropin.new"; else mv -f "$dropin.new" "$dropin" && echo "disk-fill-watch: four disks and their floors ($dropin)"; fi
fi

ensure_box_health_owner disk-fill-watch || exit 1
arm_user_timer omp disk-fill-watch
