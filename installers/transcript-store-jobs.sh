#!/bin/bash
# Arm the transcript store's import and backup timers (omp/transcript-{import,backup}.{service,timer}).
#
# Standalone installer (like omp-memory-watch.sh) — not sourced by install.sh. Run it only on the
# machine that holds the store's client and its credentials; today that is sami-rocqperf.
#
# Why this exists: both jobs ran as omp background jobs inside one agent session (the librarian's,
# 01a14ba2), re-armed by hand from a note. The 2026-10-31 reboot stopped the import from 21:46Z to
# 22:52Z, and every stream reads that store. A user timer survives a reboot, and systemd starts no
# second copy of a oneshot that is still running, which is the "two imports must never run at once"
# rule the note had to enforce by hand. dispatch://INFRA-913.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ -x "$HOME/.local/bin/transcript-store" ] || {
    echo "transcript-store-jobs: no $HOME/.local/bin/transcript-store; nothing to arm" >&2
    exit 1
}

arm_user_timer omp transcript-import
arm_user_timer omp transcript-backup
