#!/bin/bash
# Arm the daily Sami Dispatch/turn-label measurement job
# (plugins/sjawhar/skills/reflect/daily-measure.py, omp/reflect-daily.{service,timer}).
#
# Standalone installer (like omp-billing-watch.sh and hawk-token-refresh.sh) -- not
# sourced by install.sh. Run it on the machine that owns the label store
# (~/.dotfiles/.claude/reflect-store.db): sami-agents today, since that is where the
# agent-secrets broker's launcher credential lives and where the reflect skill's weekly
# run reads the store from.
#
# Why this exists: the weekly reflect run's failure-class rates (`.claude/session-
# analysis/*.md` section 1) were recomputed from scratch once a week, so a fix's effect
# never showed until the next sitting. This job extends the same two label corpora
# (classify-sami-events.py's Dispatch-event codebook, jev-turn-label.py's turn labels)
# by one day at a time, so daily-readout.py can show a fix's effect within days.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "reflect-daily: python3 not on PATH" >&2
    exit 1
}
command -v ssh >/dev/null && command -v rsync >/dev/null || {
    echo "reflect-daily: ssh and rsync are required to pull devbox-agents-2's session transcripts" >&2
    exit 1
}

arm_user_timer omp reflect-daily
