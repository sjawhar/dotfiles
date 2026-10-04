#!/bin/bash
# Arm the hourly omp API-billing watch (scripts/omp-billing-watch).
#
# Standalone installer (like voxtype.sh) — not sourced by install.sh.
# Run it on a machine that runs omp sessions; it is the machine's stats.db the
# watch reads, so there is nothing to arm on a laptop that only SSHes in.
#
# Why this exists: omp/config.yml keeps API-billed providers out of the FALLBACK
# chains, but a `/model` pick to openai/* is an explicit selection no chain can
# refuse, and a session left on one bills per token for as long as it runs. That
# is not hypothetical — two sessions picked openai/gpt-6-astra on 2026-09-19/20
# and spent ~$3.8k before anyone looked. The chain fix closed the fallback path;
# only a detector closes this one.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v sqlite3 >/dev/null || {
    echo "omp-billing-watch: sqlite3 not on PATH; the watch reads ~/.omp/stats.db" >&2
    exit 1
}

arm_user_timer omp omp-billing-watch
