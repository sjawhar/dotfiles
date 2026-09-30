#!/bin/bash
# Arm the hourly omp billing watches: scripts/omp-billing-watch (spend on
# per-token API billing) and scripts/omp-cache-watch (Anthropic prompt caching
# no longer holding, which bills whole conversations as uncached input).
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
# only a detector closes this one. The cache watch exists for the same reason:
# from 2026-09-16 to 09-28 omp sent most Anthropic turns without message cache
# breakpoints, 56-91% of each day's top-level Anthropic cost was uncached input,
# and nothing noticed it for two weeks.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v sqlite3 >/dev/null || {
    echo "omp-billing-watch: sqlite3 not on PATH; the watches read ~/.omp/stats.db" >&2
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
for watch in omp-billing-watch omp-cache-watch; do
    ensure_link "${DOTFILES_DIR}/omp/${watch}.service" ~/.config/systemd/user/${watch}.service
    ensure_link "${DOTFILES_DIR}/omp/${watch}.timer" ~/.config/systemd/user/${watch}.timer
done

systemctl --user daemon-reload
for watch in omp-billing-watch omp-cache-watch; do
    systemctl --user enable --now ${watch}.timer

    # Prove the timer is actually scheduled. `enable --now` on a timer whose unit
    # fails to load still exits 0 on some systemd versions, which would leave this
    # installer claiming an armed detector that never runs. Captured before the
    # grep: under pipefail, `grep -q` quitting at the row can SIGPIPE systemctl's
    # footer write and fail a timer that did register (seen 2026-09-30).
    listed="$(systemctl --user list-timers --all ${watch}.timer)"
    grep -q ${watch} <<<"$listed" || {
        echo "${watch}: timer did not register; see systemctl --user status ${watch}.timer" >&2
        exit 1
    }
    echo "${watch}: armed ($(systemctl --user show -p NextElapseUSecRealtime --value ${watch}.timer))"
done
