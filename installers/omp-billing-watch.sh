#!/bin/bash
# Arm the hourly omp API-billing watch (scripts/omp-billing-watch).
#
# Standalone installer (like voxtype.sh) — not sourced by install.sh.
# Run it on a machine that runs omp sessions; it is the machine's stats.db the
# watch reads, so there is nothing to arm on a laptop that only SSHes in.
#
# Why this exists: a session on openai/* or opencode-zen/* (a /model pick, the
# gpt-api or astra-api agent, or the end of a Codex fallback chain) bills per
# token for as long as it runs, and nothing else shows that spend.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v sqlite3 >/dev/null || {
    echo "omp-billing-watch: sqlite3 not on PATH; the watch reads ~/.omp/stats.db" >&2
    exit 1
}

arm_user_timer omp omp-billing-watch
