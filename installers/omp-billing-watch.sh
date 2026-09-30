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

# Must run on the host, not inside an agentbox. A box reaches the HOST's systemd
# user manager over the relayed session bus, but its ~/.config/systemd is the
# container's own -- so `systemctl enable` writes a unit the manager will never
# load, while `systemctl cat` reads it back happily because that half is
# client-side. The result looks armed and is not. Refuse instead.
if [[ -f /.dockerenv ]] || [[ "$(systemd-detect-virt 2>/dev/null)" == docker ]]; then
    echo "omp-billing-watch: this is an agentbox; the host systemd cannot see its ~/.config/systemd." >&2
    echo "  Run installers/omp-billing-watch.sh from a host shell instead." >&2
    exit 1
fi

mkdir -p ~/.config/systemd/user
ensure_link "${DOTFILES_DIR}/omp/omp-billing-watch.service" ~/.config/systemd/user/omp-billing-watch.service
ensure_link "${DOTFILES_DIR}/omp/omp-billing-watch.timer" ~/.config/systemd/user/omp-billing-watch.timer

systemctl --user daemon-reload
systemctl --user enable --now omp-billing-watch.timer

# Prove the timer is actually scheduled. `enable --now` on a timer whose unit
# fails to load still exits 0 on some systemd versions, which would leave this
# installer claiming an armed detector that never runs. Captured before the
# grep: under pipefail, `grep -q` quitting at the row can SIGPIPE systemctl's
# footer write and fail a timer that did register (seen 2026-09-30).
listed="$(systemctl --user list-timers --all omp-billing-watch.timer)"
grep -q omp-billing-watch <<<"$listed" || {
    echo "omp-billing-watch: timer did not register; see systemctl --user status omp-billing-watch.timer" >&2
    exit 1
}
echo "omp-billing-watch: armed ($(systemctl --user show -p NextElapseUSecRealtime --value omp-billing-watch.timer))"
