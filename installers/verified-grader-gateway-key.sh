#!/bin/bash
# Arm the boot-only unit that puts the BET-59 verified-grader stream's model-gateway key back in
# tmpfs (scripts/verified-grader-gateway-key, omp/verified-grader-gateway-key.service).
#
# Standalone installer — not sourced by install.sh. Run it on sami-devbox, where the `pbc-gateway`
# provider of ~/.omp/profiles/pbc/agent/models.overlay.yml reads the key; the script reaches the
# secret through the gateway VM with bin/ossv-vm, so no other machine can run it.
#
# It replaces the second step of the ossv-box-gateway-key timer, which was removed with the rest of
# the gateway credential refreshers (dispatch://INFRA-993/comment/be112388). There is no timer here
# because the key is a static secret: the only thing that loses it is a reboot emptying tmpfs. The
# unit also goes away once the gateway serves team `verified-grader` to an identity caller, which is
# the INFRA lead's change (model-gateway/shared/boxaccounts) — then the provider mints its own token
# with scripts/gateway-token, as every other gateway provider on this box already does.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

case "$(hostname -s)" in
    sami-devbox) ;;
    *)
        echo "verified-grader-gateway-key: only sami-devbox runs the verified-grader stream's sessions" >&2
        exit 1
        ;;
esac
[ -x "$HOME/src/oss-verifier/bin/ossv-vm" ] || {
    echo "verified-grader-gateway-key: no ~/src/oss-verifier/bin/ossv-vm, which the script reads the key through" >&2
    exit 1
}

# arm_user_timer wants a .timer beside the .service; this unit has none, so the same steps by hand.
# The link comes from ~/.dotfiles, not DOTFILES_DIR, so a unit armed from a workspace checkout does
# not point into a directory that will be removed.
unit="$HOME/.dotfiles/omp/verified-grader-gateway-key.service"
if [[ -f /.dockerenv ]] || [[ "$(systemd-detect-virt 2> /dev/null)" == docker ]]; then
    echo "verified-grader-gateway-key: this is an agentbox; the host systemd cannot see its ~/.config/systemd." >&2
    echo "  Run $0 from a host shell instead." >&2
    exit 1
fi
[ -f "$unit" ] || {
    echo "verified-grader-gateway-key: $unit is missing; advance ~/.dotfiles to a main that has it" >&2
    exit 1
}
mkdir -p "$HOME/.config/systemd/user"
ensure_link "$unit" "$HOME/.config/systemd/user/verified-grader-gateway-key.service"
systemctl --user daemon-reload
systemctl --user enable --now verified-grader-gateway-key.service
# `enable --now` on a oneshot that failed still exits 0 on some systemd versions, so read the result
# back rather than claim it.
state="$(systemctl --user show -p Result --value verified-grader-gateway-key.service)"
[ "$state" = success ] || {
    echo "verified-grader-gateway-key: the unit did not succeed ($state); see systemctl --user status verified-grader-gateway-key.service" >&2
    exit 1
}
echo "verified-grader-gateway-key: armed (runs at boot; Result=$state)"
