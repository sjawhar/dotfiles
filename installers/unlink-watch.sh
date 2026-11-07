#!/bin/bash
# Arm the shared-scratch delete watch (scripts/unlink-watch.bt).
#
# Standalone installer (like token-tripwire.sh) — not sourced by install.sh. Run it on a machine
# whose /mnt/nvme/<user>/tmp many sessions share: it traces THIS kernel, so there is nothing to
# arm on a laptop that only SSHes in.
#
# Why this exists: on 2026-11-07 the whole contents of /mnt/nvme/sami/tmp/CICDLead were deleted
# between 20:05:20Z and about 20:08Z, and nothing on the box could say by what. No container died
# in the window (docker events), every host cleaner had skipped its interval (box-cleanup
# reclaimed 0 B), and the kernel keeps no record of an unlink. It took the owning session reading
# its own worker's transcript to find the `rm -rf`. inotify cannot answer it either: it reports
# the path and not the process.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v bpftrace >/dev/null || {
    echo "unlink-watch: bpftrace is not installed; the watch is a bpftrace program" >&2
    exit 1
}
sudo -n true 2>/dev/null || {
    echo "unlink-watch: sudo without a password is not available here, and bpftrace needs root" >&2
    echo "  (CAP_BPF and CAP_PERFMON). Arm it by hand with systemd-run, or give this user" >&2
    echo "  a passwordless rule for bpftrace." >&2
    exit 1
}
# The LSM path hooks are what it attaches to; a kernel without CONFIG_SECURITY_PATH has neither
# and the program would load and report nothing — the failure that looks like a quiet machine.
sudo -n bpftrace -l 'kfunc:security_path_unlink' 2>/dev/null | grep -q security_path_unlink || {
    echo "unlink-watch: this kernel does not expose kfunc:security_path_unlink;" >&2
    echo "  the watch would attach to nothing and report a clean zero forever." >&2
    exit 1
}
mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}"

arm_user_service omp unlink-watch
