#!/bin/bash
# Arm every timer in devbox/expected-timers.txt: link its units, then `enable --now`.
#
# Why this exists: on 2026-11-04 the box stalled from 16:40Z and was reset at 18:05:53Z, and
# three watches did not come back. omp-memory-watch, omp-root-hang-watch and omp-login-watch
# each had a linked .service in ~/.config/systemd/user and no linked .timer, so nothing fired
# and `systemctl --user list-timers` showed nothing. A link is not an armed timer. This
# installer asks systemd to enable and start each one, which is the step that was missing, and
# it is idempotent: enabling an enabled timer and starting a running one change nothing.
#
# Standalone installer, not sourced by install.sh. Run it on the host (never inside an
# agentbox, which has its own ~/.config/systemd that the host manager never reads) after a
# reset, after adding a name to the list, or whenever you want to be sure.
#
#   installers/expected-timers.sh            arm every timer in the list
#   installers/expected-timers.sh --check    say what is wrong, change nothing
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

list="$HOME/.dotfiles/devbox/expected-timers.txt"
check=no
[[ "${1:-}" == "--check" ]] && check=yes

[[ -r "$list" ]] || {
    echo "expected-timers: $list is missing; advance ~/.dotfiles to a main that has it" >&2
    exit 1
}
if [[ -f /.dockerenv ]] || [[ "$(systemd-detect-virt 2>/dev/null)" == docker ]]; then
    echo "expected-timers: this is an agentbox; the host systemd cannot see its ~/.config/systemd." >&2
    echo "  Run $0 from a host shell instead." >&2
    exit 1
fi

armed=0 already=0 failed=0
while read -r line; do
    # A line is a timer name, then optional key=value fields, then an optional # comment. Only
    # the name matters here; unit-health-watch reads the fields.
    name="${line%%#*}"
    name="${name%% *}"
    name="${name// /}"
    [[ -n "$name" ]] || continue
    [[ "$name" == *.timer ]] || name="$name.timer"
    base="${name%.timer}"

    # Where its unit files live, if this repository has them. A timer whose units are only in
    # ~/.config/systemd/user (a lane's own) is enabled where it stands.
    dir=""
    for candidate in omp devbox; do
        [[ -f "$HOME/.dotfiles/$candidate/$name" ]] && dir="$candidate" && break
    done

    if systemctl --user is-enabled --quiet "$name" 2>/dev/null \
        && systemctl --user is-active --quiet "$name" 2>/dev/null; then
        already=$((already + 1))
        continue
    fi
    if [[ "$check" == yes ]]; then
        echo "$name: NOT ARMED ($(systemctl --user is-enabled "$name" 2>&1 || true)/$(systemctl --user is-active "$name" 2>&1 || true))"
        failed=$((failed + 1))
        continue
    fi
    if [[ -n "$dir" ]]; then
        # Links both units and proves systemd scheduled the timer.
        if arm_user_timer "$dir" "$base"; then armed=$((armed + 1)); else failed=$((failed + 1)); fi
        continue
    fi
    if ! systemctl --user cat "$name" >/dev/null 2>&1; then
        echo "$name: no unit file in ~/.dotfiles/omp, ~/.dotfiles/devbox or this user manager." >&2
        echo "  Its owner must put the unit back, or take the name out of $list." >&2
        failed=$((failed + 1))
        continue
    fi
    if systemctl --user enable --now "$name"; then
        echo "$name: armed where it stands (its units are not in ~/.dotfiles)"
        armed=$((armed + 1))
    else
        failed=$((failed + 1))
    fi
done < "$list"

echo "expected-timers: $already already armed, $armed armed now, $failed could not be armed"
[[ "$failed" -eq 0 ]]
